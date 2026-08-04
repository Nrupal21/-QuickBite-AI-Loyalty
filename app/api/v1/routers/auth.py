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
    RefreshRequest,
    RegisterResponse,
    StatusResponse,
    TokenResponse,
    UserLogin,
    UserRegister,
    VerifyEmailResponse,
)
from app.schemas.identity import LinkIdentityRequest, LinkIdentityResponse
from app.services import identity_link_service, identity_service
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
    verification_base_url = str(request.base_url) + "api/v1/auth/verify-email"
    return await AuthService(session=session).register(payload, verification_base_url)


@router.get("/verify-email", response_model=VerifyEmailResponse, status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def verify_email(
    request: Request,
    token: str,
    session: AsyncSession = Depends(get_db),
) -> VerifyEmailResponse:
    return await AuthService(session=session).verify_email(token)


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
