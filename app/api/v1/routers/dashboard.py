"""QuickBite — Dashboard routes: GET /dashboard/stats, WS /dashboard/stream (DASH-01).

`GET /stats` is the poll; the WebSocket is the push side that tells an open
dashboard tab to re-poll (see app/core/broadcast.py's module docstring for
why Redis pub/sub and why per-tenant channels).

WebSocket auth is hand-rolled rather than reusing `Depends(get_current_user)`:
that dependency's signature takes `request: Request`, which a WebSocket route
never receives (it gets a `WebSocket` instead), so it cannot be wired in as a
normal FastAPI dependency here. The steps mirror `_resolve_local` in
`app/api/v1/dependencies/auth.py` (decode, jti blocklist, tenant bind, load
user, is_active, revocation watermark) at v1 scope — Supabase/Firebase tokens
are not accepted on this endpoint yet, only locally-minted owner/staff JWTs.
"""

import uuid
from datetime import datetime, timedelta, timezone

import structlog
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies.auth import require_role
from app.core import broadcast, cache_service
from app.core.rbac import RoleLevel
from app.core.security import decode_access_token
from app.db import rls
from app.db.base import get_db
from app.db.models.user import Role, User
from app.schemas.dashboard import DashboardStatsResponse
from app.services import dashboard_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Same tolerance _resolve_local uses for the revocation-watermark comparison.
_IAT_LEEWAY = timedelta(seconds=30)


@router.get("/stats", response_model=DashboardStatsResponse, status_code=status.HTTP_200_OK)
async def get_dashboard_stats(
    current_user: User = Depends(require_role(RoleLevel.STAFF)),
    session: AsyncSession = Depends(get_db),
) -> DashboardStatsResponse:
    role_result = await session.execute(select(Role.level).where(Role.id == current_user.role_id))
    role_level = role_result.scalar_one_or_none()
    can_approve = role_level is not None and role_level <= RoleLevel.MANAGER
    return await dashboard_service.get_stats(session, can_approve=can_approve)


async def _authenticate_websocket(websocket: WebSocket, session: AsyncSession) -> User | None:
    """Returns the authenticated staff User, or None after closing the socket
    with a 401-equivalent close code (WebSocket has no HTTP status line)."""
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=4401)
        return None

    claims = decode_access_token(token)
    if claims is None:
        await websocket.close(code=4401)
        return None

    jti = claims.get("jti")
    if jti and await cache_service.exists(f"revoked_jti:{jti}"):
        await websocket.close(code=4401)
        return None

    try:
        user_id = uuid.UUID(claims["sub"])
        tenant_id = uuid.UUID(claims["tenant_id"])
    except (KeyError, ValueError, TypeError):
        await websocket.close(code=4401)
        return None

    await rls.set_tenant_context(session, tenant_id)
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        await websocket.close(code=4401)
        return None

    iat = claims.get("iat")
    if iat is None:
        await websocket.close(code=4401)
        return None
    valid_from = user.tokens_valid_from
    if valid_from is not None:
        if valid_from.tzinfo is None:
            valid_from = valid_from.replace(tzinfo=timezone.utc)
        issued = datetime.fromtimestamp(int(iat), tz=timezone.utc)
        if issued + _IAT_LEEWAY < valid_from:
            await websocket.close(code=4401)
            return None

    return user


@router.websocket("/stream")
async def dashboard_stream(
    websocket: WebSocket, session: AsyncSession = Depends(get_db)
) -> None:
    """Auth happens before `accept()` — a socket that never gets accepted
    costs the caller nothing to retry and never enters the subscribe loop."""
    user = await _authenticate_websocket(websocket, session)
    if user is None:
        return

    await websocket.accept()
    logger.info("dashboard.stream.connected", tenant_id=str(user.tenant_id))

    try:
        async with broadcast.subscribe(user.tenant_id) as events:
            async for event in events:
                await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        logger.info("dashboard.stream.disconnected", tenant_id=str(user.tenant_id))
