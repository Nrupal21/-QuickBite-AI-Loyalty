"""QuickBite — Team management: invite, accept, list, deactivate (AUTH-04).

Before this, `AuthService.verify_email()` was the only path that created a
User and it always assigned the OWNER role — so a MANAGER or STAFF account
could not exist and `require_role()` had nothing to discriminate between.
This module is what makes role-based access real.

Doc 3's permission matrix grants "invite / remove team members" to OWNER
(and SUPER_ADMIN) only; the routes enforce that with `require_role`. Two
escalation guards sit below that:

1. Only MANAGER and STAFF can be invited (INVITABLE_ROLE_NAMES) — an Owner
   minting a second Owner or a Super Admin is lateral/vertical escalation.
2. A member can only be deactivated by someone strictly more senior, so no
   one can remove a peer (or themselves) out of the tenant.

A pending invite lives in Redis rather than the DB, mirroring AUTH-01's
`pending_reg:` flow: an abandoned invite expires on its own and never leaves
a half-built User row behind.
"""

import json
import uuid
from datetime import UTC, datetime

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache_service
from app.core.encryption import decrypt_pii, encrypt_pii, sha256_hex
from app.core.rbac import INVITABLE_ROLE_NAMES
from app.core.security import generate_verification_token, hash_password
from app.db import rls
from app.db.models.audit import AuditLog
from app.db.models.user import Role, Session, User
from app.schemas.team import (
    AcceptInviteRequest,
    AcceptInviteResponse,
    DeactivateMemberResponse,
    StaffInviteRequest,
    StaffInviteResponse,
    TeamListResponse,
    TeamMemberResponse,
)
from app.services import messaging_service
from app.services.auth_service import check_password_strength

logger = structlog.get_logger(__name__)

INVITE_TTL_SECONDS = 604800  # 7 days


class TeamService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def invite(
        self, request: StaffInviteRequest, inviter: User, accept_base_url: str
    ) -> StaffInviteResponse:
        # Re-checked here even though the schema is a Literal — the schema
        # protects the HTTP boundary, this protects any future caller.
        if request.role not in INVITABLE_ROLE_NAMES:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "ROLE_NOT_INVITABLE",
                        "message": "You can only invite Managers and Staff.",
                    }
                },
            )

        role = await self._get_role_by_name(request.role)
        email = request.email.lower()
        email_hash = sha256_hex(email)

        if await self._email_exists(email_hash):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "EMAIL_ALREADY_REGISTERED",
                        "message": "Someone with this email already has an account.",
                    }
                },
            )

        token = generate_verification_token()
        # tenant_id is captured from the *inviter*, never from the request, so
        # an invite can only ever land in the tenant that issued it.
        await cache_service.set(
            f"staff_invite:{token}",
            json.dumps(
                {
                    "email": email,
                    "tenant_id": str(inviter.tenant_id),
                    "role_id": str(role.id),
                    "role_name": role.name,
                    "invited_by": str(inviter.id),
                }
            ),
            ttl=INVITE_TTL_SECONDS,
        )

        email_sent = await messaging_service.send_staff_invite_email(
            email, f"{accept_base_url}?token={token}", role.name
        )

        self.session.add(
            AuditLog(
                tenant_id=inviter.tenant_id,
                user_id=inviter.id,
                action="staff_invited",
                resource_type="user",
                event_metadata={
                    "email_hash": email_hash,
                    "role": role.name,
                    "email_sent": email_sent,
                },
            )
        )
        await self.session.commit()

        logger.info(
            "team.invite.sent",
            tenant_id=str(inviter.tenant_id),
            role=role.name,
            email_sent=email_sent,
        )
        return StaffInviteResponse(
            status="invite_sent", email=email, role=role.name, expires_in=INVITE_TTL_SECONDS
        )

    async def accept_invite(self, request: AcceptInviteRequest) -> AcceptInviteResponse:
        raw = await cache_service.get(f"staff_invite:{request.token}")
        if raw is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": "INVITE_TOKEN_INVALID",
                        "message": "This invite link is invalid or has expired.",
                    }
                },
            )

        check_password_strength(request.password)
        invite = json.loads(raw)
        email_hash = sha256_hex(invite["email"])

        # The address may have been registered between invite and click.
        if await self._email_exists(email_hash):
            await cache_service.delete(f"staff_invite:{request.token}")
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "EMAIL_ALREADY_REGISTERED",
                        "message": "Someone with this email already has an account.",
                    }
                },
            )

        username = request.username.lower() if request.username else None
        username_hash = sha256_hex(username) if username else None
        if username_hash and await self._username_exists(username_hash):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "USERNAME_ALREADY_TAKEN",
                        "message": "That username is already taken.",
                    }
                },
            )

        # Same pre-existing gap as AuthService.complete_authentication (found
        # via real end-to-end testing, not introduced by this branch): the
        # invitee has no session yet, so nothing had ever bound app.tenant_id
        # before the AuditLog insert below, which carries a real tenant_id.
        # RLS's WITH CHECK rejects it, so accepting an invite always 500s.
        invite_tenant_id = uuid.UUID(invite["tenant_id"])
        await rls.set_tenant_context(self.session, invite_tenant_id)

        role = await self._get_role_by_id(uuid.UUID(invite["role_id"]))
        user = User(
            id=uuid.uuid4(),
            tenant_id=invite_tenant_id,
            role_id=role.id,
            email_hash=email_hash,
            encrypted_email=encrypt_pii(invite["email"]),
            username_hash=username_hash,
            encrypted_username=encrypt_pii(username) if username else None,
            hashed_password=hash_password(request.password),
            # Opening a link sent only to that mailbox is the same proof of
            # control that AUTH-01's verification email demands.
            email_verified=True,
            is_active=True,
        )
        self.session.add(user)
        # Flush before the AuditLog insert below: AuditLog.user_id FKs onto
        # this row, and there is no ORM relationship() connecting the two
        # mapped classes for SQLAlchemy's automatic dependency-sort to use,
        # so without this the two inserts can reach Postgres out of order —
        # found via real end-to-end testing (not introduced by this branch).
        await self.session.flush()
        self.session.add(
            AuditLog(
                tenant_id=user.tenant_id,
                user_id=user.id,
                action="staff_invite_accepted",
                resource_type="user",
                resource_id=user.id,
                event_metadata={"role": role.name, "invited_by": invite["invited_by"]},
            )
        )
        await self.session.commit()
        await cache_service.delete(f"staff_invite:{request.token}")

        logger.info(
            "team.invite.accepted",
            tenant_id=str(user.tenant_id),
            user_id=str(user.id),
            role=role.name,
        )
        return AcceptInviteResponse(
            status="account_created", role=role.name, mfa_required=role.mfa_required
        )

    async def list_members(self, current_user: User) -> TeamListResponse:
        result = await self.session.execute(
            select(User, Role)
            .join(Role, Role.id == User.role_id)
            .where(User.tenant_id == current_user.tenant_id)
            .order_by(Role.level)
        )
        members = [
            TeamMemberResponse(
                user_id=user.id,
                email=decrypt_pii(user.encrypted_email),
                username=decrypt_pii(user.encrypted_username) if user.encrypted_username else None,
                role=role.name,
                role_level=role.level,
                mfa_enabled=user.mfa_enabled,
                email_verified=user.email_verified,
                is_active=user.is_active,
            )
            for user, role in result.all()
        ]
        return TeamListResponse(members=members)

    async def deactivate(self, user_id: uuid.UUID, actor: User) -> DeactivateMemberResponse:
        if user_id == actor.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": {
                        "code": "CANNOT_DEACTIVATE_SELF",
                        "message": "You can't remove your own account.",
                    }
                },
            )

        target = await self._get_user_by_id(user_id)
        # Same 404 for "no such user" and "other tenant's user" — a distinct
        # error would confirm an id exists on the platform. RLS should already
        # hide the row; this is the belt to that braces.
        if target is None or target.tenant_id != actor.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": {
                        "code": "TEAM_MEMBER_NOT_FOUND",
                        "message": "No such team member.",
                    }
                },
            )

        actor_role = await self._get_role_by_id(actor.role_id)
        target_role = await self._get_role_by_id(target.role_id)
        # Strictly more senior, not just senior-or-equal: an Owner must not be
        # able to remove another Owner and take sole control of the tenant.
        if actor_role.level >= target_role.level:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": {
                        "code": "INSUFFICIENT_PERMISSIONS",
                        "message": "You can only remove team members junior to you.",
                    }
                },
            )

        target.is_active = False
        # is_active=False already blocks a Supabase/Firebase-linked login too
        # (identity_link_service re-checks it on every external resolve), but
        # bump the watermark as well: it is the one revocation path that does
        # not depend on remembering to add an is_active check at every new
        # call site, and it is what a future password/MFA reset should reuse.
        target.tokens_valid_from = datetime.now(UTC)
        # Their access token stays signed and unexpired for up to its TTL, so
        # revoking refresh sessions alone is not enough — get_current_user()
        # rejects is_active=False on every request, which closes that window.
        sessions = await self.session.execute(
            select(Session).where(Session.user_id == target.id, Session.revoked.is_(False))
        )
        for active_session in sessions.scalars().all():
            active_session.revoked = True

        self.session.add(
            AuditLog(
                tenant_id=actor.tenant_id,
                user_id=actor.id,
                action="staff_deactivated",
                resource_type="user",
                resource_id=target.id,
                event_metadata={"role": target_role.name},
            )
        )
        await self.session.commit()

        logger.info(
            "team.member.deactivated",
            tenant_id=str(actor.tenant_id),
            actor_id=str(actor.id),
            target_id=str(target.id),
        )
        return DeactivateMemberResponse(status="deactivated", user_id=target.id)

    # --- Lookups ---------------------------------------------------------

    async def _email_exists(self, email_hash: str) -> bool:
        result = await self.session.execute(select(User.id).where(User.email_hash == email_hash))
        return result.scalar_one_or_none() is not None

    async def _username_exists(self, username_hash: str) -> bool:
        result = await self.session.execute(
            select(User.id).where(User.username_hash == username_hash)
        )
        return result.scalar_one_or_none() is not None

    async def _get_user_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self.session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def _get_role_by_id(self, role_id: uuid.UUID) -> Role:
        result = await self.session.execute(select(Role).where(Role.id == role_id))
        role = result.scalar_one_or_none()
        if role is None:
            msg = f"Role {role_id} referenced by a user but missing from static.roles"
            raise RuntimeError(msg)
        return role

    async def _get_role_by_name(self, name: str) -> Role:
        result = await self.session.execute(select(Role).where(Role.name == name))
        role = result.scalar_one_or_none()
        if role is None:
            msg = f"{name} role missing — run scripts/seed_roles.py"
            raise RuntimeError(msg)
        return role
