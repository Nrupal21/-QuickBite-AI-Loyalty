"""QuickBite — WhatsApp marketing campaign business logic.

Owns the WABA connect/disconnect/template-sync flows and campaign creation,
launch, and webhook-driven status updates. `whatsapp_service.py` stays the
thin Graph API wire client (mirrors gmb_service.py / review_sync_service.py's
split); this module is where DB rows get written.

Every audience resolution hard-filters on Customer.whatsapp_marketing_opt_in
being True — `audience_filter` narrows *within* that opted-in set, it never
widens past it. That floor is the one compliance guarantee this file cannot
be wrong about.
"""

import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.encryption import decrypt_pii, encrypt_pii, sha256_hex
from app.db import rls
from app.db.models.customer import Customer
from app.db.models.whatsapp import (
    CampaignRecipient,
    MarketingCampaign,
    WhatsAppBusinessAccount,
    WhatsAppTemplate,
)
from app.services import whatsapp_service
from app.services.whatsapp_service import WhatsAppOAuthError, WhatsAppSendError

logger = structlog.get_logger(__name__)

# Meta's rate-limit error codes — a send hitting one of these means "stop and
# retry later", not "this recipient failed"; the Celery task backs off rather
# than marking every remaining recipient as failed.
RATE_LIMIT_ERROR_CODES = {"131056", "131048"}

MARKETING_FEATURE = "whatsapp_marketing"


class WhatsAppNotConnected(RuntimeError):
    """No connected WhatsAppBusinessAccount for this tenant."""


class CampaignNotFound(RuntimeError):
    pass


class InvalidTemplate(RuntimeError):
    """The chosen template doesn't belong to this tenant, or isn't an
    approved MARKETING-category template."""


# --- Connect / disconnect ----------------------------------------------------


async def connect_whatsapp_account(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    code: str,
    waba_id: str,
    phone_number_id: str,
    business_id: str,
) -> WhatsAppBusinessAccount:
    """Finish Embedded Signup: exchange the code, pull the phone number's
    display details, subscribe webhooks, and upsert the tenant's account."""
    short_lived = await whatsapp_service.exchange_code_for_token(code)
    long_lived = await whatsapp_service.exchange_for_long_lived_token(short_lived["access_token"])
    access_token = long_lived["access_token"]

    phone_details = await whatsapp_service.fetch_phone_number_details(phone_number_id, access_token)
    display_number = phone_details.get("display_phone_number", "")

    try:
        await whatsapp_service.subscribe_app_to_waba(waba_id, access_token)
        webhook_subscribed = True
    except WhatsAppOAuthError:
        # A tenant should still be able to connect and send even if the
        # webhook subscribe call has a transient failure — delivery/read
        # receipts simply won't update until a retry (see sync route).
        logger.warning("whatsapp.webhook_subscribe_failed", tenant_id=str(tenant_id))
        webhook_subscribed = False

    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(WhatsAppBusinessAccount).where(WhatsAppBusinessAccount.tenant_id == tenant_id)
    )
    account = result.scalar_one_or_none()
    if account is None:
        account = WhatsAppBusinessAccount(tenant_id=tenant_id)
        session.add(account)

    account.waba_id = waba_id
    account.phone_number_id = phone_number_id
    account.business_id = business_id
    account.phone_hash = sha256_hex(display_number)
    account.encrypted_display_phone_number = encrypt_pii(display_number)
    account.encrypted_access_token = encrypt_pii(access_token)
    account.token_exchanged_at = now
    account.quality_rating = phone_details.get("quality_rating")
    account.messaging_tier = phone_details.get("messaging_limit_tier")
    account.webhook_subscribed = webhook_subscribed
    account.is_connected = True
    account.connected_at = now
    await session.commit()

    logger.info("whatsapp.connected", tenant_id=str(tenant_id))
    return account


async def disconnect_whatsapp_account(
    session: AsyncSession, tenant_id: uuid.UUID
) -> WhatsAppBusinessAccount | None:
    result = await session.execute(
        select(WhatsAppBusinessAccount).where(WhatsAppBusinessAccount.tenant_id == tenant_id)
    )
    account = result.scalar_one_or_none()
    if account is None:
        return None

    account.is_connected = False
    account.encrypted_access_token = ""
    await session.commit()

    logger.info("whatsapp.disconnected", tenant_id=str(tenant_id))
    return account


async def get_connected_account(
    session: AsyncSession, tenant_id: uuid.UUID
) -> WhatsAppBusinessAccount:
    result = await session.execute(
        select(WhatsAppBusinessAccount).where(
            WhatsAppBusinessAccount.tenant_id == tenant_id,
            WhatsAppBusinessAccount.is_connected.is_(True),
        )
    )
    account = result.scalar_one_or_none()
    if account is None:
        raise WhatsAppNotConnected
    return account


# --- Templates ----------------------------------------------------------


async def sync_templates(session: AsyncSession, tenant_id: uuid.UUID) -> list[WhatsAppTemplate]:
    account = await get_connected_account(session, tenant_id)
    access_token = decrypt_pii(account.encrypted_access_token)
    remote_templates = await whatsapp_service.fetch_templates(account.waba_id, access_token)

    now = datetime.now(timezone.utc)
    synced: list[WhatsAppTemplate] = []
    for remote in remote_templates:
        result = await session.execute(
            select(WhatsAppTemplate).where(
                WhatsAppTemplate.tenant_id == tenant_id,
                WhatsAppTemplate.name == remote["name"],
                WhatsAppTemplate.language == remote["language"],
            )
        )
        template = result.scalar_one_or_none()
        if template is None:
            template = WhatsAppTemplate(tenant_id=tenant_id, waba_account_id=account.id)
            session.add(template)

        template.meta_template_id = remote["id"]
        template.name = remote["name"]
        template.language = remote["language"]
        template.category = remote["category"]
        template.status = remote["status"]
        template.components = remote.get("components", [])
        template.last_synced_at = now
        synced.append(template)

    await session.commit()
    logger.info("whatsapp.templates.synced", tenant_id=str(tenant_id), count=len(synced))
    return synced


async def list_templates(session: AsyncSession, tenant_id: uuid.UUID) -> list[WhatsAppTemplate]:
    result = await session.execute(
        select(WhatsAppTemplate).where(WhatsAppTemplate.tenant_id == tenant_id)
    )
    return list(result.scalars().all())


async def get_default_utility_template(
    session: AsyncSession, tenant_id: uuid.UUID
) -> WhatsAppTemplate | None:
    """The tenant's synced UTILITY template for single-body-variable
    transactional alerts (reward-unlocked etc.) — see
    messaging_service.send_whatsapp for how the one parameter is filled."""
    result = await session.execute(
        select(WhatsAppTemplate)
        .where(
            WhatsAppTemplate.tenant_id == tenant_id,
            WhatsAppTemplate.category == "UTILITY",
            WhatsAppTemplate.status == "APPROVED",
        )
        .limit(1)
    )
    return result.scalar_one_or_none()


# --- Campaigns ------------------------------------------------------------


async def resolve_audience(
    session: AsyncSession, tenant_id: uuid.UUID, audience_filter: dict
) -> list[Customer]:
    query = select(Customer).where(
        Customer.tenant_id == tenant_id,
        Customer.whatsapp_marketing_opt_in.is_(True),
        Customer.is_blocked.is_(False),
    )
    min_stamps = audience_filter.get("min_total_stamps")
    if isinstance(min_stamps, int):
        query = query.where(Customer.total_stamps_alltime >= min_stamps)

    result = await session.execute(query)
    return list(result.scalars().all())


async def create_campaign(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    created_by_user_id: uuid.UUID,
    name: str,
    template_id: uuid.UUID,
    audience_filter: dict,
    branch_id: uuid.UUID | None = None,
) -> MarketingCampaign:
    result = await session.execute(
        select(WhatsAppTemplate).where(
            WhatsAppTemplate.id == template_id, WhatsAppTemplate.tenant_id == tenant_id
        )
    )
    template = result.scalar_one_or_none()
    if template is None or template.category != "MARKETING" or template.status != "APPROVED":
        raise InvalidTemplate

    campaign = MarketingCampaign(
        tenant_id=tenant_id,
        branch_id=branch_id,
        name=name,
        template_id=template_id,
        audience_filter=audience_filter,
        created_by_user_id=created_by_user_id,
        status="draft",
    )
    session.add(campaign)
    await session.commit()

    logger.info("marketing.campaign.created", tenant_id=str(tenant_id), campaign_id=str(campaign.id))
    return campaign


async def get_campaign(
    session: AsyncSession, tenant_id: uuid.UUID, campaign_id: uuid.UUID
) -> MarketingCampaign:
    result = await session.execute(
        select(MarketingCampaign).where(
            MarketingCampaign.id == campaign_id, MarketingCampaign.tenant_id == tenant_id
        )
    )
    campaign = result.scalar_one_or_none()
    if campaign is None:
        raise CampaignNotFound
    return campaign


async def list_campaigns(session: AsyncSession, tenant_id: uuid.UUID) -> list[MarketingCampaign]:
    result = await session.execute(
        select(MarketingCampaign).where(MarketingCampaign.tenant_id == tenant_id)
    )
    return list(result.scalars().all())


async def launch_campaign(
    session: AsyncSession, tenant_id: uuid.UUID, campaign_id: uuid.UUID
) -> MarketingCampaign:
    """Resolve the audience, create one CampaignRecipient per customer, and
    hand off to the Celery fan-out task. The unique (campaign_id, customer_id)
    constraint means calling this twice for the same campaign is safe — the
    second call's audience rows just fail to insert on conflict."""
    campaign = await get_campaign(session, tenant_id, campaign_id)
    if campaign.status != "draft":
        return campaign

    customers = await resolve_audience(session, tenant_id, campaign.audience_filter)
    for customer in customers:
        session.add(
            CampaignRecipient(tenant_id=tenant_id, campaign_id=campaign.id, customer_id=customer.id)
        )

    campaign.recipients_total = len(customers)
    campaign.status = "sending"
    await session.commit()

    from app.workers.tasks import send_campaign_task  # noqa: PLC0415 — avoid a Celery import at module load

    send_campaign_task.delay(str(campaign.id))

    logger.info(
        "marketing.campaign.launched",
        tenant_id=str(tenant_id),
        campaign_id=str(campaign.id),
        recipients=len(customers),
    )
    return campaign


async def _resolve_campaign_tenant(session: AsyncSession, campaign_id: uuid.UUID) -> uuid.UUID | None:
    """No tenant context exists yet on a bare Celery task — same gap
    admin_bypass_context exists for. Scoped to this one lookup only."""
    async with rls.admin_bypass_context(session):
        result = await session.execute(
            select(MarketingCampaign.tenant_id).where(MarketingCampaign.id == campaign_id)
        )
        return result.scalar_one_or_none()


async def send_campaign_batch(session: AsyncSession, campaign_id: uuid.UUID) -> int:
    """Send every still-queued recipient of one campaign. Called by the
    Celery task, one campaign per invocation — no authenticated request has
    bound tenant context here, so the whole batch runs inside an explicit
    `rls.tenant_context`, the same pattern the Razorpay webhook and outbox
    drain use. Stops (rather than marking the rest failed) the moment Meta
    reports a rate limit, so the task can retry the remainder later instead
    of burning through the whole audience as failures the instant a tenant's
    messaging tier is hit."""
    tenant_id = await _resolve_campaign_tenant(session, campaign_id)
    if tenant_id is None:
        return 0

    async with rls.tenant_context(session, tenant_id):
        result = await session.execute(
            select(MarketingCampaign).where(MarketingCampaign.id == campaign_id)
        )
        campaign = result.scalar_one_or_none()
        if campaign is None:
            return 0

        account = await get_connected_account(session, campaign.tenant_id)
        access_token = decrypt_pii(account.encrypted_access_token)

        template_result = await session.execute(
            select(WhatsAppTemplate).where(WhatsAppTemplate.id == campaign.template_id)
        )
        template = template_result.scalar_one()

        recipients_result = await session.execute(
            select(CampaignRecipient, Customer)
            .join(Customer, Customer.id == CampaignRecipient.customer_id)
            .where(CampaignRecipient.campaign_id == campaign_id, CampaignRecipient.status == "queued")
        )
        rows = recipients_result.all()

        sent = 0
        for recipient, customer in rows:
            phone = decrypt_pii(customer.encrypted_phone)
            try:
                wa_message_id = await whatsapp_service.send_template_message(
                    account.phone_number_id,
                    access_token,
                    phone,
                    template.name,
                    template.language,
                )
            except WhatsAppSendError as exc:
                if exc.error_code in RATE_LIMIT_ERROR_CODES:
                    logger.warning(
                        "marketing.campaign.rate_limited", campaign_id=str(campaign_id), sent=sent
                    )
                    break
                recipient.status = "failed"
                recipient.error_code = exc.error_code
                recipient.error_message = str(exc)
                campaign.failed_count += 1
                continue

            recipient.status = "sent"
            recipient.wa_message_id = wa_message_id
            recipient.sent_at = datetime.now(timezone.utc)
            campaign.sent_count += 1
            sent += 1

        remaining_result = await session.execute(
            select(CampaignRecipient.id).where(
                CampaignRecipient.campaign_id == campaign_id, CampaignRecipient.status == "queued"
            )
        )
        if remaining_result.first() is None:
            campaign.status = "completed"

        await session.commit()

    logger.info("marketing.campaign.batch_done", campaign_id=str(campaign_id), sent=sent)
    return sent


# --- Webhook-driven updates ------------------------------------------------


async def apply_delivery_status(
    session: AsyncSession, wa_message_id: str, status_value: str, timestamp: datetime
) -> None:
    result = await session.execute(
        select(CampaignRecipient).where(CampaignRecipient.wa_message_id == wa_message_id)
    )
    recipient = result.scalar_one_or_none()
    if recipient is None:
        return

    campaign_result = await session.execute(
        select(MarketingCampaign).where(MarketingCampaign.id == recipient.campaign_id)
    )
    campaign = campaign_result.scalar_one_or_none()

    if status_value == "delivered" and recipient.status != "delivered":
        recipient.status = "delivered"
        recipient.delivered_at = timestamp
        if campaign is not None:
            campaign.delivered_count += 1
    elif status_value == "read" and recipient.status != "read":
        recipient.status = "read"
        recipient.read_at = timestamp
        if campaign is not None:
            campaign.read_count += 1
    elif status_value == "failed":
        recipient.status = "failed"

    await session.commit()


async def apply_opt_out(session: AsyncSession, tenant_id: uuid.UUID, phone: str) -> None:
    """Flip whatsapp_marketing_opt_in off for a customer who replied STOP or
    tapped a template's opt-out button. Never touches whatsapp_opt_in
    (transactional alerts) — those are separate consents."""
    phone_hash = sha256_hex(phone)
    result = await session.execute(
        select(Customer).where(Customer.tenant_id == tenant_id, Customer.phone_hash == phone_hash)
    )
    customer = result.scalar_one_or_none()
    if customer is None:
        return

    customer.whatsapp_marketing_opt_in = False
    await session.commit()
    logger.info("marketing.opt_out", tenant_id=str(tenant_id), customer_id=str(customer.id))


_OPT_OUT_KEYWORDS = {"stop", "unsubscribe"}


def _is_opt_out_message(message: dict) -> bool:
    if message.get("type") == "text":
        body = message.get("text", {}).get("body", "").strip().lower()
        return body in _OPT_OUT_KEYWORDS
    if message.get("type") == "button":
        # Meta's template opt-out quick-reply button.
        return message.get("button", {}).get("text", "").strip().lower() in _OPT_OUT_KEYWORDS
    return False


async def _resolve_tenant_by_phone_number_id(
    session: AsyncSession, phone_number_id: str
) -> uuid.UUID | None:
    """No tenant context exists yet on a webhook request — the same "who is
    this, before we know their tenant" gap admin_bypass_context exists for
    (see migration 0015's docstring). Scoped to this one lookup only."""
    async with rls.admin_bypass_context(session):
        result = await session.execute(
            select(WhatsAppBusinessAccount.tenant_id).where(
                WhatsAppBusinessAccount.phone_number_id == phone_number_id
            )
        )
        return result.scalar_one_or_none()


async def process_webhook_payload(session: AsyncSession, payload: dict) -> None:
    """Entry point for POST /webhooks/whatsapp. Meta batches every WABA this
    app is subscribed to into one envelope — walk every entry/change and
    route delivery receipts and inbound opt-outs to the right tenant."""
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            phone_number_id = value.get("metadata", {}).get("phone_number_id")
            if not phone_number_id:
                continue

            tenant_id = await _resolve_tenant_by_phone_number_id(session, phone_number_id)
            if tenant_id is None:
                continue

            async with rls.tenant_context(session, tenant_id):
                for status_event in value.get("statuses", []):
                    timestamp = datetime.fromtimestamp(
                        int(status_event["timestamp"]), tz=timezone.utc
                    )
                    await apply_delivery_status(
                        session, status_event["id"], status_event["status"], timestamp
                    )

                for message in value.get("messages", []):
                    if _is_opt_out_message(message):
                        await apply_opt_out(session, tenant_id, message["from"])
