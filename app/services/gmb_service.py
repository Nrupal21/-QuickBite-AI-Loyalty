"""QuickBite — Google My Business integration: OAuth + reply posting.

REVIEW-02 built reply posting assuming a `GMBProfile` already carried a
usable access token. REVIEW-03 fills the gap it left behind: the OAuth
authorization-code flow that actually connects a branch (`build_authorize_url`
/ `complete_connect`), refreshing an expired access token from the stored
refresh token (`get_valid_access_token`), and disconnecting
(`disconnect`). Inbound review pulling itself lives in
`review_sync_service.py` — this module stays about the GMB HTTP client, not
about what QuickBite does with the reviews it returns.
"""

import json
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache_service
from app.core.config import settings
from app.core.encryption import decrypt_pii, encrypt_pii
from app.db.models.reputation import GMBProfile

logger = structlog.get_logger(__name__)

_GMB_REPLY_URL = (
    "https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{location_id}/reviews/{review_id}/reply"
)
_GMB_REVIEWS_URL = (
    "https://mybusiness.googleapis.com/v4/accounts/{account_id}/locations/{location_id}/reviews"
)
_GMB_ACCOUNTS_URL = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"
_GMB_LOCATIONS_URL = "https://mybusinessbusinessinformation.googleapis.com/v1/{account_name}/locations"
_OAUTH_AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
_OAUTH_REVOKE_URL = "https://oauth2.googleapis.com/revoke"
_OAUTH_SCOPE = "https://www.googleapis.com/auth/business.manage"

_REQUEST_TIMEOUT_SECONDS = 10.0

# A stored token is treated as expired this far before its real expiry, so a
# request never races Google's clock and gets a 401 mid-call.
_TOKEN_REFRESH_SKEW_SECONDS = 60

# CSRF state for the OAuth redirect: minted by /gmb/connect for an
# authenticated Owner, single-use, and short-lived — the browser's top-level
# redirect to Google and back cannot carry a bearer token, so this Redis
# round-trip is what authorises the callback instead.
_OAUTH_STATE_KEY_PREFIX = "gmb_oauth_state"
_OAUTH_STATE_TTL_SECONDS = 600


class GMBNotConnected(RuntimeError):
    """No GMBProfile exists for this branch — nothing to post to."""


class GMBTokenExpired(RuntimeError):
    """The stored access token has expired and could not be refreshed."""


class GMBOAuthError(RuntimeError):
    """Google rejected an OAuth exchange, refresh, or the account/location lookup.

    `revoked` distinguishes "the refresh token itself is dead" (Google's
    `invalid_grant`) from every other failure — only that case should flip
    `GMBProfile.is_connected` and surface a reconnect banner; a transient
    network or 5xx error should not disconnect the branch.
    """

    def __init__(self, message: str, *, revoked: bool = False) -> None:
        super().__init__(message)
        self.revoked = revoked


async def post_review_reply(profile: GMBProfile, external_review_id: str, reply_text: str) -> None:
    """POST an owner reply to a Google review via the GMB API.

    Raises on any failure — the caller decides whether that means "retry the
    Celery task" or "log and give up", not this function. Deliberately does
    not auto-refresh (see `get_valid_access_token` for that): REVIEW-02's
    tests pin this function to raising `GMBTokenExpired` on a stale stored
    token rather than reaching out to Google mid-post.
    """
    if not profile.is_connected:
        raise GMBNotConnected

    if profile.token_expires_at <= datetime.now(timezone.utc):
        raise GMBTokenExpired

    access_token = decrypt_pii(profile.encrypted_access_token)
    url = _GMB_REPLY_URL.format(
        account_id=profile.gmb_account_id,
        location_id=profile.gmb_location_id,
        review_id=external_review_id,
    )

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.put(
            url,
            headers={"Authorization": f"Bearer {access_token}"},
            json={"comment": reply_text},
        )
    response.raise_for_status()

    logger.info(
        "gmb.reply.posted",
        branch_id=str(profile.branch_id),
        tenant_id=str(profile.tenant_id),
    )


# --- OAuth: connect ----------------------------------------------------------


def build_authorize_url(state: str) -> str:
    """The URL to send the owner's browser to start the consent screen."""
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": settings.GMB_OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": _OAUTH_SCOPE,
        "access_type": "offline",
        # Forces Google to hand back a refresh_token even if this Google
        # account already granted consent once before — without it a
        # reconnect after a revoke silently gets no refresh_token at all.
        "prompt": "consent",
        "state": state,
    }
    return f"{_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


async def create_oauth_state(tenant_id: uuid.UUID, branch_id: uuid.UUID) -> str:
    """Mint a single-use CSRF token binding this connect attempt to a branch."""
    state = secrets.token_urlsafe(32)
    await cache_service.set(
        f"{_OAUTH_STATE_KEY_PREFIX}:{state}",
        json.dumps({"tenant_id": str(tenant_id), "branch_id": str(branch_id)}),
        ttl=_OAUTH_STATE_TTL_SECONDS,
    )
    return state


async def consume_oauth_state(state: str) -> dict[str, str] | None:
    """Look up and delete a state token. `None` means expired, unknown, or replayed."""
    key = f"{_OAUTH_STATE_KEY_PREFIX}:{state}"
    raw = await cache_service.get(key)
    if raw is None:
        return None
    await cache_service.delete(key)
    return json.loads(raw)


async def exchange_code_for_tokens(code: str) -> dict:
    """Trade an authorization code for an access + refresh token pair."""
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                _OAUTH_TOKEN_URL,
                data={
                    "code": code,
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "redirect_uri": settings.GMB_OAUTH_REDIRECT_URI,
                    "grant_type": "authorization_code",
                },
            )
    except httpx.HTTPError as exc:
        raise GMBOAuthError(f"Couldn't reach Google to exchange the code: {exc}") from exc

    if response.status_code != 200:
        raise GMBOAuthError(f"GMB code exchange failed: {response.text}")
    return response.json()


async def _resolve_first_account_and_location(access_token: str) -> tuple[str, str]:
    """Pick the first GMB account + location visible to this token.

    A Google account can manage several businesses and a business can have
    several locations; a real product would let the owner choose among them.
    QuickBite's onboarding connects one branch to one location, so taking the
    first of each keeps the flow a single redirect round-trip — an owner with
    more than one Google-side location reconnects the same way to pick a
    different one, tracked as a follow-up rather than blocking REVIEW-03.

    Every Google call here is wrapped: an owner whose Google account has no
    Business Profile access (no GMB API grant, wrong Google account, API not
    enabled on our Cloud project) gets a clean `GMBOAuthError` — caught by the
    callback route into a 400 — rather than an unhandled `httpx.HTTPStatusError`
    surfacing as a raw 500 mid-redirect.
    """
    headers = {"Authorization": f"Bearer {access_token}"}
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            accounts_response = await client.get(_GMB_ACCOUNTS_URL, headers=headers)
            accounts_response.raise_for_status()
            accounts = accounts_response.json().get("accounts", [])
            if not accounts:
                raise GMBOAuthError("This Google account has no linked Business Profile accounts")
            account_name = accounts[0]["name"]  # "accounts/12345"

            locations_response = await client.get(
                _GMB_LOCATIONS_URL.format(account_name=account_name),
                headers=headers,
                params={"readMask": "name"},
            )
            locations_response.raise_for_status()
            locations = locations_response.json().get("locations", [])
            if not locations:
                raise GMBOAuthError("This Business Profile account has no locations")
            location_name = locations[0]["name"]  # "locations/67890"
    except httpx.HTTPError as exc:
        raise GMBOAuthError(f"Couldn't read Google Business Profile accounts/locations: {exc}") from exc

    return account_name.removeprefix("accounts/"), location_name.removeprefix("locations/")


async def complete_connect(
    session: AsyncSession, *, code: str, tenant_id: uuid.UUID, branch_id: uuid.UUID
) -> GMBProfile:
    """Finish the OAuth callback: exchange the code, resolve the location,
    and upsert the branch's `GMBProfile`. Caller must have already bound
    `tenant_id` for RLS (the callback has no bearer token to do it for us)."""
    token_data = await exchange_code_for_tokens(code)
    access_token = token_data["access_token"]
    refresh_token = token_data.get("refresh_token")
    if not refresh_token:
        raise GMBOAuthError("Google did not return a refresh token")

    account_id, location_id = await _resolve_first_account_and_location(access_token)

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=token_data.get("expires_in", 3600))

    result = await session.execute(
        select(GMBProfile).where(GMBProfile.branch_id == branch_id, GMBProfile.tenant_id == tenant_id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        profile = GMBProfile(tenant_id=tenant_id, branch_id=branch_id)
        session.add(profile)

    profile.gmb_account_id = account_id
    profile.gmb_location_id = location_id
    profile.encrypted_access_token = encrypt_pii(access_token)
    profile.encrypted_refresh_token = encrypt_pii(refresh_token)
    profile.token_expires_at = expires_at
    profile.token_rotated_at = now
    profile.is_connected = True
    await session.commit()

    logger.info("gmb.oauth.connected", branch_id=str(branch_id), tenant_id=str(tenant_id))
    return profile


# --- OAuth: refresh ----------------------------------------------------------


async def refresh_access_token(refresh_token: str) -> dict:
    """Trade a refresh token for a new access token. Raises `GMBOAuthError`,
    `revoked=True` when Google reports `invalid_grant` (the refresh token
    itself has been revoked or expired — the caller must disconnect)."""
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                _OAUTH_TOKEN_URL,
                data={
                    "refresh_token": refresh_token,
                    "client_id": settings.GOOGLE_CLIENT_ID,
                    "client_secret": settings.GOOGLE_CLIENT_SECRET,
                    "grant_type": "refresh_token",
                },
            )
    except httpx.HTTPError as exc:
        raise GMBOAuthError(f"Couldn't reach Google to refresh the token: {exc}") from exc

    if response.status_code != 200:
        revoked = False
        try:
            revoked = response.json().get("error") == "invalid_grant"
        except ValueError:
            pass
        raise GMBOAuthError(f"GMB token refresh failed: {response.text}", revoked=revoked)
    return response.json()


async def get_valid_access_token(session: AsyncSession, profile: GMBProfile) -> str:
    """Return a usable access token, refreshing and persisting it first if the
    stored one is stale. Unlike `post_review_reply`, this path (used by the
    inbound review sync) is expected to self-heal rather than fail on an
    hour-old token — sync runs on a schedule, not on an owner's click."""
    if not profile.is_connected:
        raise GMBNotConnected

    now = datetime.now(timezone.utc)
    if profile.token_expires_at > now + timedelta(seconds=_TOKEN_REFRESH_SKEW_SECONDS):
        return decrypt_pii(profile.encrypted_access_token)

    try:
        token_data = await refresh_access_token(decrypt_pii(profile.encrypted_refresh_token))
    except GMBOAuthError as exc:
        if exc.revoked:
            profile.is_connected = False
            await session.commit()
            logger.warning(
                "gmb.token.revoked", branch_id=str(profile.branch_id), tenant_id=str(profile.tenant_id)
            )
        raise GMBTokenExpired from exc

    profile.encrypted_access_token = encrypt_pii(token_data["access_token"])
    profile.token_expires_at = now + timedelta(seconds=token_data.get("expires_in", 3600))
    profile.token_rotated_at = now
    await session.commit()

    logger.info("gmb.token.refreshed", branch_id=str(profile.branch_id), tenant_id=str(profile.tenant_id))
    return decrypt_pii(profile.encrypted_access_token)


# --- OAuth: disconnect --------------------------------------------------------


async def disconnect(session: AsyncSession, tenant_id: uuid.UUID, branch_id: uuid.UUID) -> GMBProfile | None:
    """Revoke the token at Google (best-effort) and mark the branch disconnected.

    Returns `None` when no profile exists at all, so the route can 404. Wipes
    the stored ciphertext rather than merely flipping `is_connected` — a
    revoked token has no business value, and clearing it removes an OAuth
    secret from the database the moment it stops being needed.
    """
    result = await session.execute(
        select(GMBProfile).where(GMBProfile.branch_id == branch_id, GMBProfile.tenant_id == tenant_id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        return None
    if not profile.is_connected:
        return profile

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            revoke_response = await client.post(
                _OAUTH_REVOKE_URL, params={"token": decrypt_pii(profile.encrypted_refresh_token)}
            )
        revoke_response.raise_for_status()
    except httpx.HTTPError:
        # Google being unreachable must not block the owner from disconnecting
        # locally — an un-revoked token simply expires on its own in an hour
        # and is never refreshed again since is_connected is about to go False.
        logger.warning("gmb.oauth.revoke_failed", branch_id=str(branch_id), tenant_id=str(tenant_id))

    profile.is_connected = False
    profile.encrypted_access_token = ""
    profile.encrypted_refresh_token = ""
    await session.commit()

    logger.info("gmb.oauth.disconnected", branch_id=str(branch_id), tenant_id=str(tenant_id))
    return profile


# --- Inbound reviews (used by review_sync_service) ---------------------------


async def fetch_reviews(access_token: str, account_id: str, location_id: str, page_token: str | None = None) -> dict:
    """One page of a location's reviews, newest first."""
    params: dict[str, str] = {"pageSize": "50"}
    if page_token:
        params["pageToken"] = page_token

    url = _GMB_REVIEWS_URL.format(account_id=account_id, location_id=location_id)
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.get(url, headers={"Authorization": f"Bearer {access_token}"}, params=params)
    response.raise_for_status()
    return response.json()
