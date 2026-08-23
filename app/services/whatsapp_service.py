"""QuickBite — Meta WhatsApp Business Platform (Cloud API) client.

Per-tenant, bring-your-own WABA connected via Meta's Embedded Signup JS SDK.
Unlike GMB's OAuth (gmb_service.py), Embedded Signup needs no server-side
redirect callback: the tenant's already-authenticated dashboard tab opens a
Facebook popup, and the resulting `code` + `waba_id` + `phone_number_id` are
delivered straight back to that same tab via `window.postMessage` — so
`complete_connect` below is called directly off an authenticated route, no
CSRF `state` token required (there is no cross-page redirect to protect).

Token exchange trades the signup `code` for a 60-day long-lived user access
token (`fb_exchange_token`), not a permanent System User token — that needs
Meta "Tech Provider" assignment of a system user onto the client's WABA via
the Business Manager API, out of scope for v1. A tenant's connection lapses
after ~60 days and needs re-running Embedded Signup; token_exchanged_at is
stored so a background check can warn a tenant before that happens.

This module owns the Graph API wire calls only — no DB access, no template
copy. `campaign_service.py` and `messaging_service.py` are the callers.
"""

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

_REQUEST_TIMEOUT_SECONDS = 10.0


def _graph_url(path: str) -> str:
    return f"https://graph.facebook.com/{settings.META_GRAPH_API_VERSION}/{path}"


class WhatsAppOAuthError(RuntimeError):
    """Meta rejected a code exchange, token refresh, or webhook subscription."""


class WhatsAppSendError(RuntimeError):
    """Meta rejected a message send. `error_code` is Meta's own numeric code
    (e.g. 131056/131048 for rate limits) — callers use it to decide whether
    to back off and retry or give up on this recipient."""

    def __init__(self, message: str, *, error_code: str | None = None) -> None:
        super().__init__(message)
        self.error_code = error_code


async def exchange_code_for_token(code: str) -> dict:
    """Trade an Embedded Signup `code` for a short-lived user access token."""
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(
                _graph_url("oauth/access_token"),
                params={
                    "client_id": settings.META_APP_ID,
                    "client_secret": settings.META_APP_SECRET,
                    "code": code,
                },
            )
    except httpx.HTTPError as exc:
        raise WhatsAppOAuthError(f"Couldn't reach Meta to exchange the code: {exc}") from exc

    if response.status_code != 200:
        raise WhatsAppOAuthError(f"WhatsApp code exchange failed: {response.text}")
    return response.json()


async def exchange_for_long_lived_token(short_lived_token: str) -> dict:
    """Trade a short-lived token for a ~60-day long-lived one."""
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(
                _graph_url("oauth/access_token"),
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": settings.META_APP_ID,
                    "client_secret": settings.META_APP_SECRET,
                    "fb_exchange_token": short_lived_token,
                },
            )
    except httpx.HTTPError as exc:
        raise WhatsAppOAuthError(f"Couldn't reach Meta to extend the token: {exc}") from exc

    if response.status_code != 200:
        raise WhatsAppOAuthError(f"WhatsApp token extension failed: {response.text}")
    return response.json()


async def subscribe_app_to_waba(waba_id: str, access_token: str) -> None:
    """Subscribe this app to the WABA's webhooks — required once per WABA
    before Meta will deliver delivery/read receipts or inbound events."""
    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                _graph_url(f"{waba_id}/subscribed_apps"),
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.HTTPError as exc:
        raise WhatsAppOAuthError(f"Couldn't reach Meta to subscribe the app: {exc}") from exc

    if response.status_code != 200:
        raise WhatsAppOAuthError(f"WhatsApp app subscription failed: {response.text}")


async def fetch_phone_number_details(phone_number_id: str, access_token: str) -> dict:
    """Display number + Meta's quality/messaging-tier signals for one number."""
    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
        response = await client.get(
            _graph_url(phone_number_id),
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": "display_phone_number,quality_rating,messaging_limit_tier"},
        )
    response.raise_for_status()
    return response.json()


async def fetch_templates(waba_id: str, access_token: str) -> list[dict]:
    """All message templates on this WABA, newest first, across every page."""
    templates: list[dict] = []
    url = _graph_url(f"{waba_id}/message_templates")
    params: dict[str, str] | None = {
        "fields": "id,name,language,category,status,components",
        "limit": "100",
    }

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
        while url:
            response = await client.get(
                url, headers={"Authorization": f"Bearer {access_token}"}, params=params
            )
            response.raise_for_status()
            payload = response.json()
            templates.extend(payload.get("data", []))
            url = payload.get("paging", {}).get("next")
            params = None  # `next` is already a fully-qualified URL with its own query string

    return templates


async def send_template_message(
    phone_number_id: str,
    access_token: str,
    to: str,
    template_name: str,
    language: str,
    components: list[dict] | None = None,
) -> str:
    """Send one template message. Returns Meta's `wamid` on success.

    Raises `WhatsAppSendError` on any failure — best-effort/bool-return is
    campaign_service's job (it has a CampaignRecipient row to mark failed),
    this stays a thin, raising wrapper like GMB's post_review_reply.
    """
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
            "components": components or [],
        },
    }

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.post(
                _graph_url(f"{phone_number_id}/messages"),
                headers={"Authorization": f"Bearer {access_token}"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise WhatsAppSendError(f"Couldn't reach Meta to send the message: {exc}") from exc

    if response.status_code != 200:
        error_code = None
        try:
            error_code = str(response.json().get("error", {}).get("code", ""))
        except ValueError:
            pass
        raise WhatsAppSendError(f"WhatsApp send failed: {response.text}", error_code=error_code)

    data = response.json()
    return data["messages"][0]["id"]


def verify_webhook_challenge(mode: str | None, token: str | None, challenge: str | None) -> str | None:
    """Meta's `GET /webhooks/whatsapp` subscription handshake. Returns the
    challenge to echo back, or None when the token doesn't match (caller 403s)."""
    if mode == "subscribe" and token == settings.META_WEBHOOK_VERIFY_TOKEN and challenge:
        return challenge
    return None
