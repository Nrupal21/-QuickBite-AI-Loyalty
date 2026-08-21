"""QuickBite — Owner/Staff auth routes: register, login, mfa, refresh, logout.

AUTH-01: registration + email verification. AUTH-02: login + TOTP MFA.
AUTH-03: refresh token rotation + logout/logout-all. /identify is the
unified identify-first entry point shared by the Owner/Staff and Customer
login flows (see identity_service.py) — not a documented ticket.

AUTH-04 adds /me (role + permissions for the dashboard) and the TOTP
enrollment trio that Doc 3's "MFA REQUIRED for Owner/Manager/Super Admin"
rule needs in order to be enforceable at login.
"""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies.auth import get_current_user
from app.core.encryption import sha256_hex
from app.core.principal import AuthProvider
from app.core.rate_limiter import limiter
from app.db.base import get_db
from app.db.models.user import User
from app.schemas.auth import (
    BecomeRestaurantRequest,
    BecomeRestaurantResponse,
    CompleteOAuthRegistrationRequest,
    CompleteOtpRegistrationRequest,
    ContactOtpRequestRequest,
    ContactOtpRequestResponse,
    ContactOtpVerifyRequest,
    ContactOtpVerifyResponse,
    IdentifyRequest,
    IdentifyResponse,
    MeResponse,
    MFAChallengeResponse,
    MFAConfirmRequest,
    MFAEnrollmentRequiredResponse,
    MFAEnrollRequest,
    MFAEnrollResponse,
    MFAStartResponse,
    MFAVerify,
    OAuthRegistrationRequiredResponse,
    OtpRegistrationRequiredResponse,
    RefreshRequest,
    RegisterResponse,
    StatusResponse,
    TenantLookupResponse,
    TokenResponse,
    UserLogin,
    UserOAuthSignInRequest,
    UserOtpRequestRequest,
    UserOtpSentResponse,
    UserOtpVerifyRequest,
    UserRegister,
    VerifyEmailResponse,
)
from app.schemas.identity import LinkIdentityRequest, LinkIdentityResponse
from app.services import (
    identity_link_service,
    identity_service,
    user_oauth_service,
    user_otp_service,
)
from app.services.auth_service import AuthService

router = APIRouter(prefix="/auth", tags=["auth"])


def _ip_hash(request: Request) -> str:
    client_host = request.client.host if request.client else "unknown"
    return sha256_hex(client_host)


@router.post("/identify", response_model=IdentifyResponse, status_code=status.HTTP_200_OK)
@limiter.limit("30/minute")
async def identify(
    request: Request,
    payload: IdentifyRequest,
    session: AsyncSession = Depends(get_db),
) -> IdentifyResponse:
    return await identity_service.identify(payload, session)


@router.post("/register", response_model=RegisterResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def register(
    request: Request,
    payload: UserRegister,
    session: AsyncSession = Depends(get_db),
) -> RegisterResponse:
    # Points at the HTML page route (pages.py), not this API's own JSON
    # endpoint below — a human clicks this link from their inbox and needs a
    # rendered confirmation, not a raw JSON body. The JSON endpoint stays for
    # programmatic callers (tests, API clients).
    verification_base_url = str(request.base_url) + "verify-email"
    return await AuthService(session=session).register(payload, verification_base_url)


@router.get("/tenant", response_model=TenantLookupResponse, status_code=status.HTTP_200_OK)
@limiter.limit("20/minute")
async def lookup_tenant(
    request: Request,
    subdomain: str,
    session: AsyncSession = Depends(get_db),
) -> TenantLookupResponse:
    """Resolves a restaurant's subdomain to its tenant_id — the identify-first
    login screen's 'find your restaurant' step (see /login's find-tenant
    step in auth-login.js) uses this until TENANT-01 adds Host-header
    routing."""
    return await AuthService(session=session).lookup_tenant(subdomain)


@router.get("/verify-email", response_model=VerifyEmailResponse, status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def verify_email(
    request: Request,
    token: str,
    session: AsyncSession = Depends(get_db),
) -> VerifyEmailResponse:
    return await AuthService(session=session).verify_email(token)


@router.post(
    "/register-restaurant/contact-otp/request",
    response_model=ContactOtpRequestResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("5/minute")
async def request_restaurant_contact_otp(
    request: Request,
    payload: ContactOtpRequestRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ContactOtpRequestResponse:
    """"Join Us" step: send an OTP to the second contact method a standard
    user is registering their business with.

    Which method that is depends on how they signed up — an email-registered
    user verifies a phone here, a phone-registered user verifies an email —
    so the endpoint takes a generic `contact` and classifies it itself.
    """
    return await AuthService(session=session).request_contact_otp(current_user, payload)


@router.post(
    "/register-restaurant/contact-otp/verify",
    response_model=ContactOtpVerifyResponse,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("10/minute")
async def verify_restaurant_contact_otp(
    request: Request,
    payload: ContactOtpVerifyRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> ContactOtpVerifyResponse:
    return await AuthService(session=session).verify_contact_otp(current_user, payload)


@router.post("/register-restaurant", status_code=status.HTTP_201_CREATED)
@limiter.limit("5/hour")
async def register_restaurant(
    request: Request,
    payload: BecomeRestaurantRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MFAEnrollmentRequiredResponse | BecomeRestaurantResponse:
    """A standard user's one-time upgrade to Owner: creates their Tenant,
    promotes their role, and issues a fresh token pair — or, if Owner's
    mandatory MFA isn't enrolled yet, an enrollment challenge instead (AUTH-04:
    "MFA REQUIRED for Owner" applies here exactly as it does at login)."""
    return await AuthService(session=session).become_restaurant(
        current_user, payload, _ip_hash(request)
    )


@router.post(
    "/otp/request", response_model=UserOtpSentResponse, status_code=status.HTTP_200_OK
)
@limiter.limit("10/minute")
async def request_login_otp(
    request: Request,
    payload: UserOtpRequestRequest,
    session: AsyncSession = Depends(get_db),
) -> UserOtpSentResponse:
    """Send a one-time sign-in code to an email address or phone number.

    Answers identically whether or not an account exists — see
    user_otp_service's module docstring. The same endpoint serves sign-in and
    sign-up, because from the caller's side those are the same action until
    the code is entered.
    """
    return await user_otp_service.request_otp(payload, session)


@router.post("/otp/verify", status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def verify_login_otp(
    request: Request,
    payload: UserOtpVerifyRequest,
    session: AsyncSession = Depends(get_db),
) -> (
    MFAChallengeResponse
    | MFAEnrollmentRequiredResponse
    | TokenResponse
    | OtpRegistrationRequiredResponse
):
    """Sign in with the code, or receive a token to finish signing up."""
    return await user_otp_service.verify_otp(payload, session, _ip_hash(request))


@router.post("/otp/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("5/minute")
async def complete_otp_registration(
    request: Request,
    payload: CompleteOtpRegistrationRequest,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Create the account a verified code pre-authorised, and sign it in.

    `account_url` is built from the request, not from config, for the same
    reason /auth/register builds its verification link that way: the right
    host is whichever host the browser actually reached.
    """
    account_url = str(request.base_url) + "profile"
    return await user_otp_service.complete_registration(
        payload, session, _ip_hash(request), account_url
    )


@router.post("/oauth", status_code=status.HTTP_200_OK)
@limiter.limit("20/minute")
async def oauth_sign_in(
    request: Request,
    payload: UserOAuthSignInRequest,
    session: AsyncSession = Depends(get_db),
) -> (
    MFAChallengeResponse
    | MFAEnrollmentRequiredResponse
    | TokenResponse
    | OAuthRegistrationRequiredResponse
):
    """Sign in with Google, Apple, Microsoft, GitHub, or Twitter/X.

    Distinct from /auth/customer/oauth, which signs in a loyalty diner. Same
    provider, same token shape, entirely different principal — see
    user_oauth_service.
    """
    return await user_oauth_service.sign_in(payload, session, _ip_hash(request))


@router.post(
    "/oauth/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED
)
@limiter.limit("5/minute")
async def complete_oauth_registration(
    request: Request,
    payload: CompleteOAuthRegistrationRequest,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    account_url = str(request.base_url) + "profile"
    return await user_oauth_service.complete_registration(
        payload, session, _ip_hash(request), account_url
    )


@router.post("/login", status_code=status.HTTP_200_OK)
@limiter.limit("5/15minute")
async def login(
    request: Request,
    payload: UserLogin,
    session: AsyncSession = Depends(get_db),
) -> MFAChallengeResponse | MFAEnrollmentRequiredResponse | TokenResponse:
    return await AuthService(session=session).login(payload, _ip_hash(request))


@router.post("/mfa/verify", response_model=TokenResponse, status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def verify_mfa(
    request: Request,
    payload: MFAVerify,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    return await AuthService(session=session).verify_mfa(payload, _ip_hash(request))


@router.post("/mfa/start", response_model=MFAStartResponse, status_code=status.HTTP_200_OK)
@limiter.limit("5/minute")
async def start_mfa_enrollment(
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MFAStartResponse:
    """Opt in to MFA while already logged in — for roles where Doc 3 makes it
    optional (Staff). Roles that require MFA get their enrollment session from
    /auth/login instead, since they never receive an access token to reach here."""
    return await AuthService(session=session).start_mfa_enrollment(current_user)


@router.post("/mfa/enroll", response_model=MFAEnrollResponse, status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def enroll_mfa(
    request: Request,
    payload: MFAEnrollRequest,
    session: AsyncSession = Depends(get_db),
) -> MFAEnrollResponse:
    """Authenticated by the enroll-purpose mfa_session_token in the body, not a
    bearer token — an Owner blocked at login has no access token yet."""
    return await AuthService(session=session).enroll_mfa(payload)


@router.post("/mfa/confirm", response_model=TokenResponse, status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def confirm_mfa(
    request: Request,
    payload: MFAConfirmRequest,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    return await AuthService(session=session).confirm_mfa(payload, _ip_hash(request))


@router.get("/me", response_model=MeResponse, status_code=status.HTTP_200_OK)
async def read_current_user(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> MeResponse:
    return await AuthService(session=session).me(current_user)


@router.post("/token/refresh", response_model=TokenResponse, status_code=status.HTTP_200_OK)
@limiter.limit("20/minute")
async def refresh_token(
    request: Request,
    payload: RefreshRequest,
    session: AsyncSession = Depends(get_db),
) -> TokenResponse:
    user_agent = request.headers.get("User-Agent", "")
    return await AuthService(session=session).refresh(payload, _ip_hash(request), user_agent)


@router.post("/logout", response_model=StatusResponse, status_code=status.HTTP_200_OK)
async def logout(
    request: Request,
    payload: RefreshRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> StatusResponse:
    claims = request.state.jwt_claims
    exp = datetime.fromtimestamp(claims["exp"], tz=timezone.utc)
    return await AuthService(session=session).logout(
        jti=claims["jti"], exp=exp, refresh_token=payload.refresh_token
    )


@router.post("/logout-all", response_model=StatusResponse, status_code=status.HTTP_200_OK)
async def logout_all(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> StatusResponse:
    return await AuthService(session=session).logout_all(current_user.id)


@router.post(
    "/link/{provider}", response_model=LinkIdentityResponse, status_code=status.HTTP_201_CREATED
)
@limiter.limit("10/hour")
async def link_identity(
    request: Request,
    provider: Literal["supabase", "firebase"],
    payload: LinkIdentityRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> LinkIdentityResponse:
    """Attach a verified Supabase or Firebase identity to the caller's account.

    Requires BOTH a valid local session (proves who the caller already is) and
    a valid external token in the body (proves they control that provider
    account) — this is the only sanctioned way a staff account gains an
    external identity. Never auto-linked from a token's email claim: see
    identity_link_service for why that is an account-takeover path.
    """
    auth_provider = AuthProvider(provider)
    if auth_provider is AuthProvider.SUPABASE:
        from app.core.supabase_auth import verify_supabase_token  # noqa: PLC0415

        claims = await verify_supabase_token(payload.external_token)
    else:
        from app.core.firebase_auth import verify_firebase_token  # noqa: PLC0415

        claims = await verify_firebase_token(payload.external_token)

    subject = claims.get("sub")
    if not subject:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "INVALID_TOKEN",
                    "message": "Your session has expired. Please log in again.",
                }
            },
        )

    await identity_link_service.link_to_user(session, current_user, auth_provider, str(subject))
    return LinkIdentityResponse(provider=provider)
