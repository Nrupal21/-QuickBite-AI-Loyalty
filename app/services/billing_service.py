"""QuickBite — Razorpay subscriptions + webhooks.

Postgres is the payment source of truth; Firestore is a read-only mirror kept
current by `projection_service` draining `payment.projection_outbox` — see
that module for why a transactional outbox exists instead of a direct write.

Tenant resolution for an inbound webhook is the one genuinely tricky part.
Row-Level Security means every tenant-scoped table requires `app.tenant_id`
to already be set before it can be queried — including the very
`payment.subscriptions` table you might reach for to answer "which tenant is
this Razorpay subscription for?". That is a chicken-and-egg RLS cannot resolve
on its own, and `SET row_security = off` is forbidden outright (AGENTS.md
§3). The way out is to never need that lookup: every subscription this
service creates carries `notes: {"tenant_id": ...}`, and Razorpay echoes
`notes` back verbatim on every subsequent webhook for that entity. Tenant
resolution is therefore reading our own note back, not querying Postgres.
"""

import functools
import hashlib
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import anyio.to_thread
import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import razorpay_signature
from app.core.config import settings
from app.db import rls
from app.db.models.audit import AuditLog
from app.db.models.outbox import ProjectionOutbox
from app.db.models.payment import BillingEvent
from app.db.models.static_data import PlanCategory
from app.db.models.subscription import Subscription, SubscriptionPlan
from app.db.models.tenant import Tenant
from app.db.models.user import User
from app.schemas.billing import CheckoutResponse, PlanOut, SubscriptionStatusResponse
from app.workers.tasks import finalize_subscription_cancellation

logger = structlog.get_logger(__name__)

# Razorpay does not guarantee a subscription runs forever from one API call —
# total_count is how many billing cycles to authorise up front. 120 monthly
# cycles (10 years) reads, in practice, as "until cancelled" without actually
# passing an unbounded value the API rejects.
_SUBSCRIPTION_TOTAL_COUNT = 120

# subscription.* event -> our internal Subscription.status vocabulary.
# `None` means "update period fields but leave status untouched" — several
# Razorpay events (updated, charged mid-cycle) don't represent a state
# transition on their own.
_EVENT_TO_STATUS: dict[str, str | None] = {
    "subscription.authenticated": "trialing",
    "subscription.activated": "active",
    "subscription.charged": "active",
    "subscription.completed": "active",
    "subscription.updated": None,
    "subscription.pending": "past_due",
    "subscription.halted": "past_due",
    "subscription.paused": "paused",
    "subscription.resumed": "active",
    "subscription.cancelled": "canceled",
}

_PROVIDER_UNAVAILABLE = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail={
        "error": {
            "code": "BILLING_PROVIDER_UNAVAILABLE",
            "message": "Billing is temporarily unavailable. Please try again shortly.",
        }
    },
)

_MALFORMED_WEBHOOK = HTTPException(
    status_code=status.HTTP_400_BAD_REQUEST,
    detail={
        "error": {
            "code": "WEBHOOK_MALFORMED",
            "message": "Webhook payload could not be parsed.",
        }
    },
)

_PLAN_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail={"error": {"code": "PLAN_NOT_FOUND", "message": "No such subscription plan."}},
)

_PLAN_NOT_PROVISIONED = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail={
        "error": {
            "code": "PLAN_NOT_PROVISIONED",
            "message": "This plan is not yet available for checkout.",
        }
    },
)

_SUBSCRIPTION_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail={
        "error": {
            "code": "SUBSCRIPTION_NOT_FOUND",
            "message": "This tenant has no subscription to cancel.",
        }
    },
)

_SUBSCRIPTION_ALREADY_CANCELED = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail={
        "error": {
            "code": "SUBSCRIPTION_ALREADY_CANCELED",
            "message": "This subscription is already scheduled to cancel.",
        }
    },
)

_SUBSCRIPTION_NOT_CANCELED = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail={
        "error": {
            "code": "SUBSCRIPTION_NOT_CANCELED",
            "message": "This subscription isn't scheduled to cancel.",
        }
    },
)

_SUBSCRIPTION_ALREADY_ENDED = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail={
        "error": {
            "code": "SUBSCRIPTION_ALREADY_ENDED",
            "message": "This subscription's billing period has already ended.",
        }
    },
)

_client: Any = None


def _get_client() -> Any:
    """Lazy Razorpay SDK client — mirrors firebase_auth.get_app().

    Not constructed at import: settings default RAZORPAY_KEY_ID/SECRET to
    empty, so an unconfigured environment (every test run) must never attempt
    to build a client.
    """
    global _client  # noqa: PLW0603
    if _client is not None:
        return _client
    if not (settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET):
        raise _PROVIDER_UNAVAILABLE
    import razorpay  # noqa: PLC0415 - optional heavy dependency, see firebase_auth

    _client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
    return _client


def _tenant_id_from_notes(envelope: dict[str, Any]) -> uuid.UUID | None:
    """Read tenant_id back from the `notes` we attached at subscription
    creation. The only tenant-resolution path that doesn't require RLS to
    already be satisfied — see the module docstring."""
    for entity_name in ("subscription", "payment"):
        entity = envelope.get("payload", {}).get(entity_name, {}).get("entity", {})
        notes = entity.get("notes") or {}
        raw = notes.get("tenant_id")
        if raw:
            try:
                return uuid.UUID(str(raw))
            except ValueError:
                continue
    return None


class BillingService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # --- Plans ------------------------------------------------------------

    async def list_active_plans(
        self, category_id: uuid.UUID | None = None
    ) -> list[PlanOut]:
        """Public pricing data for the registration/onboarding screens —
        static.subscription_plans carries no RLS (shared reference data), so
        this needs no tenant context and no auth.

        `category_id` narrows the list to the plans mapped to that business
        category in static.plan_categories. A category with no mapping rows
        falls through to every active plan rather than to an empty list: an
        operator who adds a category and forgets to map it should get a
        conservative pricing page, not a dead end that blocks registration.
        """
        query = (
            select(SubscriptionPlan)
            .where(SubscriptionPlan.is_active.is_(True))
            .order_by(SubscriptionPlan.price_monthly_inr)
        )
        if category_id is not None:
            mapped_ids = (
                await self.session.execute(
                    select(PlanCategory.plan_id).where(
                        PlanCategory.category_id == category_id
                    )
                )
            ).scalars().all()
            if mapped_ids:
                query = query.where(SubscriptionPlan.id.in_(mapped_ids))

        result = await self.session.execute(query)
        return [
            PlanOut(
                id=str(plan.id),
                name=plan.name,
                display_name=plan.display_name,
                price_monthly_inr=plan.price_monthly_inr,
                trial_days=plan.trial_days,
                feature_limits=plan.feature_limits,
            )
            for plan in result.scalars().all()
        ]

    # --- Subscription status -------------------------------------------

    async def get_subscription_status(self, tenant_id: uuid.UUID) -> SubscriptionStatusResponse:
        result = await self.session.execute(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
        subscription = result.scalar_one_or_none()
        if subscription is None:
            return SubscriptionStatusResponse(status="none")

        plan_result = await self.session.execute(
            select(SubscriptionPlan).where(SubscriptionPlan.id == subscription.plan_id)
        )
        plan = plan_result.scalar_one_or_none()

        return SubscriptionStatusResponse(
            status=subscription.status,
            plan_name=plan.display_name if plan else None,
            provider=subscription.provider,
            current_period_end=subscription.current_period_end,
            trial_ends_at=subscription.trial_ends_at,
            cancel_at_period_end=subscription.cancel_at_period_end,
        )

    # --- Cancel subscription --------------------------------------------------

    async def cancel_subscription(
        self, tenant_id: uuid.UUID, admin: User
    ) -> SubscriptionStatusResponse:
        """Schedule a subscription for cancellation at the current period end.

        Sets cancel_at_period_end locally and schedules finalize_subscription_cancellation
        for the period's actual end, which re-checks the flag before making the real
        Razorpay call (since Razorpay has no API to reverse a sent cancellation).
        """
        result = await self.session.execute(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
        subscription = result.scalar_one_or_none()
        if subscription is None:
            raise _SUBSCRIPTION_NOT_FOUND
        if subscription.cancel_at_period_end:
            raise _SUBSCRIPTION_ALREADY_CANCELED

        subscription.cancel_at_period_end = True
        await self.session.commit()

        finalize_subscription_cancellation.apply_async(
            args=[str(subscription.id)], eta=subscription.current_period_end
        )

        self.session.add(
            AuditLog(
                tenant_id=tenant_id,
                user_id=admin.id,
                action="billing.subscription_canceled",
                resource_type="subscription",
                resource_id=subscription.id,
                event_metadata=None,
            )
        )
        await self.session.commit()

        logger.info(
            "billing.subscription.cancel_scheduled",
            tenant_id=str(tenant_id),
            subscription_id=str(subscription.id),
        )
        return await self.get_subscription_status(tenant_id)

    async def reactivate_subscription(
        self, tenant_id: uuid.UUID, admin: User
    ) -> SubscriptionStatusResponse:
        """Undo a pending subscription cancellation.

        Clears cancel_at_period_end locally. Never calls Razorpay, since the
        deferred-cancellation design means Razorpay was never told about the
        cancellation unless the period actually ended.
        """
        result = await self.session.execute(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
        subscription = result.scalar_one_or_none()
        if subscription is None:
            raise _SUBSCRIPTION_NOT_FOUND
        if not subscription.cancel_at_period_end:
            raise _SUBSCRIPTION_NOT_CANCELED
        if subscription.current_period_end < datetime.now(UTC):
            raise _SUBSCRIPTION_ALREADY_ENDED

        subscription.cancel_at_period_end = False

        self.session.add(
            AuditLog(
                tenant_id=tenant_id,
                user_id=admin.id,
                action="billing.subscription_reactivated",
                resource_type="subscription",
                resource_id=subscription.id,
                event_metadata=None,
            )
        )
        await self.session.commit()

        logger.info(
            "billing.subscription.reactivated",
            tenant_id=str(tenant_id),
            subscription_id=str(subscription.id),
        )
        return await self.get_subscription_status(tenant_id)

    # --- Checkout ---------------------------------------------------------

    async def create_checkout_order(self, tenant: Tenant, plan_id: uuid.UUID) -> CheckoutResponse:
        plan_result = await self.session.execute(
            select(SubscriptionPlan).where(
                SubscriptionPlan.id == plan_id, SubscriptionPlan.is_active.is_(True)
            )
        )
        plan = plan_result.scalar_one_or_none()
        if plan is None:
            raise _PLAN_NOT_FOUND
        if not plan.provider_plan_id:
            raise _PLAN_NOT_PROVISIONED

        client = _get_client()
        # razorpay-python is a synchronous requests-based SDK; calling it
        # directly here would block the event loop for the round trip.
        razorpay_subscription = await anyio.to_thread.run_sync(
            functools.partial(
                client.subscription.create,
                {
                    "plan_id": plan.provider_plan_id,
                    "customer_notify": 1,
                    "total_count": _SUBSCRIPTION_TOTAL_COUNT,
                    "notes": {"tenant_id": str(tenant.id)},
                },
            )
        )

        subscription = await self._upsert_subscription(
            tenant_id=tenant.id,
            plan_id=plan.id,
            status="created",
            provider_subscription_ref=razorpay_subscription["id"],
            provider_customer_ref=razorpay_subscription.get("customer_id"),
            current_period_end=None,
        )
        await self.session.commit()
        logger.info(
            "billing.checkout.created",
            tenant_id=str(tenant.id),
            provider_subscription_ref=subscription.provider_subscription_ref,
        )
        return CheckoutResponse(
            provider_subscription_ref=razorpay_subscription["id"],
            key_id=settings.RAZORPAY_KEY_ID,
            short_url=razorpay_subscription.get("short_url"),
        )

    # --- Webhook ------------------------------------------------------------

    async def handle_webhook(self, raw_body: bytes, headers: Mapping[str, str]) -> dict[str, Any]:
        """Process one already-signature-verified Razorpay webhook delivery.

        Idempotent under at-least-once redelivery via the unique
        `provider_event_id` constraint on `billing_events` — the INSERT (not
        a prior SELECT) is the arbiter, so two concurrent deliveries of the
        same event can never both apply.
        """
        try:
            envelope = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise _MALFORMED_WEBHOOK from exc

        event_type = envelope.get("event")
        created_at = envelope.get("created_at")
        if not event_type or created_at is None:
            raise _MALFORMED_WEBHOOK

        provider_event_id = headers.get(
            razorpay_signature.EVENT_ID_HEADER
        ) or hashlib.sha256(raw_body).hexdigest()
        provider_event_at = datetime.fromtimestamp(int(created_at), tz=UTC)
        tenant_id = _tenant_id_from_notes(envelope)

        # Tenant context must be bound BEFORE the INSERT below when tenant_id
        # is known: billing_events' RLS policy allows `tenant_id IS NULL`
        # unconditionally (short-circuits before evaluating current_setting),
        # but a NOT NULL tenant_id still evaluates
        # `current_setting('app.tenant_id')`, which errors on a connection
        # that has never had it set at all. Setting it first also means this
        # write is itself RLS-checked — a wrong tenant_id here fails the
        # INSERT's WITH CHECK rather than silently mis-attributing a payment
        # event to another tenant.
        if tenant_id is not None:
            await rls.set_tenant_context(self.session, tenant_id)

        event_row = BillingEvent(
            tenant_id=tenant_id,
            provider="razorpay",
            provider_event_id=provider_event_id,
            event_type=event_type,
            payload=envelope,
            provider_event_at=provider_event_at,
        )
        self.session.add(event_row)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            logger.info(
                "billing.webhook.duplicate",
                event_type=event_type,
                provider_event_id=provider_event_id,
            )
            return {"status": "duplicate"}

        if tenant_id is None:
            await self.session.commit()
            logger.warning(
                "billing.webhook.tenant_unresolved",
                event_type=event_type,
                provider_event_id=provider_event_id,
            )
            return {"status": "received", "tenant_resolved": False}

        await self._apply_event(tenant_id, event_type, envelope, provider_event_at)
        event_row.processed = True
        await self.session.commit()
        logger.info("billing.webhook.processed", event_type=event_type, tenant_id=str(tenant_id))
        return {"status": "processed"}

    # --- Event application --------------------------------------------------

    async def _apply_event(
        self,
        tenant_id: uuid.UUID,
        event_type: str,
        envelope: dict[str, Any],
        provider_event_at: datetime,
    ) -> None:
        if event_type.startswith("subscription."):
            await self._apply_subscription_event(
                tenant_id, event_type, envelope, provider_event_at
            )
        elif event_type.startswith("payment."):
            await self._apply_payment_event(tenant_id, event_type, envelope, provider_event_at)
        else:
            logger.info("billing.webhook.event_ignored", event_type=event_type)

    async def _apply_subscription_event(
        self,
        tenant_id: uuid.UUID,
        event_type: str,
        envelope: dict[str, Any],
        provider_event_at: datetime,
    ) -> None:
        entity = envelope.get("payload", {}).get("subscription", {}).get("entity", {})
        provider_subscription_ref = entity.get("id")
        if not provider_subscription_ref:
            return

        result = await self.session.execute(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
        subscription = result.scalar_one_or_none()
        if subscription is None:
            # A webhook for a subscription this service never created — the
            # staleness guard below has nothing to compare against, but
            # storing the raw event (already done by the caller) is still
            # correct: a reconciliation pass can fill in the plan later.
            logger.warning(
                "billing.webhook.subscription_unknown",
                tenant_id=str(tenant_id),
                provider_subscription_ref=provider_subscription_ref,
            )
            return

        # Razorpay delivers at-least-once and does not guarantee order, so a
        # retried earlier event must not clobber a later one already applied.
        if (
            subscription.provider_event_at is not None
            and provider_event_at < subscription.provider_event_at
        ):
            logger.info(
                "billing.webhook.stale_event_dropped",
                event_type=event_type,
                tenant_id=str(tenant_id),
            )
            return

        new_status = _EVENT_TO_STATUS.get(event_type)
        if new_status is not None:
            subscription.status = new_status
        subscription.provider_subscription_ref = provider_subscription_ref
        subscription.provider_customer_ref = (
            entity.get("customer_id") or subscription.provider_customer_ref
        )
        subscription.provider_event_at = provider_event_at
        current_end = entity.get("current_end")
        if current_end:
            subscription.current_period_end = datetime.fromtimestamp(int(current_end), tz=UTC)
        subscription.cancel_at_period_end = bool(entity.get("cancel_at_cycle_end", 0))

        self._enqueue_projection(
            tenant_id=tenant_id,
            aggregate_type="subscription",
            aggregate_id=subscription.id,
            version=int(provider_event_at.timestamp()),
            event_type=event_type,
            payload={
                "status": subscription.status,
                "provider": subscription.provider,
                "current_period_end": subscription.current_period_end.isoformat()
                if subscription.current_period_end
                else None,
                "cancel_at_period_end": subscription.cancel_at_period_end,
            },
        )

    async def _apply_payment_event(
        self,
        tenant_id: uuid.UUID,
        event_type: str,
        envelope: dict[str, Any],
        provider_event_at: datetime,
    ) -> None:
        entity = envelope.get("payload", {}).get("payment", {}).get("entity", {})
        payment_id = entity.get("id")
        if not payment_id:
            return
        self._enqueue_projection(
            tenant_id=tenant_id,
            aggregate_type="payment",
            # Razorpay's payment id is not a UUID; derive a stable one so the
            # outbox's aggregate_id column can stay typed instead of a raw
            # string per aggregate_type.
            aggregate_id=uuid.uuid5(uuid.NAMESPACE_URL, f"razorpay:{payment_id}"),
            version=int(provider_event_at.timestamp()),
            event_type=event_type,
            payload={
                "provider_payment_ref": payment_id,
                "amount_inr": entity.get("amount"),
                "status": "captured" if event_type == "payment.captured" else "failed",
                "method": entity.get("method"),
            },
        )

    def _enqueue_projection(
        self,
        *,
        tenant_id: uuid.UUID,
        aggregate_type: str,
        aggregate_id: uuid.UUID,
        version: int,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        """Write the outbox row in the SAME transaction as the ledger change.

        This is the entire correctness argument for the Firestore mirror:
        either both this row and the Subscription update commit, or neither
        does. A worker drains it later — see projection_service.
        """
        self.session.add(
            ProjectionOutbox(
                tenant_id=tenant_id,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                version=version,
                event_type=event_type,
                payload=payload,
            )
        )

    async def _upsert_subscription(
        self,
        *,
        tenant_id: uuid.UUID,
        plan_id: uuid.UUID,
        status: str,
        provider_subscription_ref: str,
        provider_customer_ref: str | None,
        current_period_end: datetime | None,
    ) -> Subscription:
        result = await self.session.execute(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )
        subscription = result.scalar_one_or_none()
        if subscription is None:
            subscription = Subscription(
                tenant_id=tenant_id,
                plan_id=plan_id,
                status=status,
                provider="razorpay",
                current_period_end=current_period_end or datetime.now(UTC),
            )
            self.session.add(subscription)
        subscription.plan_id = plan_id
        subscription.status = status
        subscription.provider_subscription_ref = provider_subscription_ref
        subscription.provider_customer_ref = provider_customer_ref
        if current_period_end is not None:
            subscription.current_period_end = current_period_end
        return subscription
