"""QuickBite — Marketing routes: WhatsApp WABA connect/status/templates,
/campaigns/*, and /webhooks/whatsapp (unauthenticated, Meta-signed).

WhatsApp connect/disconnect and campaign create/launch are Owner+ only — a
WABA connection carries real message-sending cost and a campaign reaches a
tenant's whole customer list, the same risk class as GMB connect/disconnect
(reputation.py). Status/template reads and campaign listing are Manager+,
matching the reviews list/approve split.

The webhook route mirrors billing.py's Razorpay webhook: no `get_current_user`
dependency (there is no bearer token on a server-to-server callback), the raw
body is read and its `X-Hub-Signature-256` verified before anything parses it,
and the actual status/opt-out processing is handed to a Celery task so Meta
gets its 200 back immediately — Meta disables a subscription that responds
too slowly.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies.auth import require_role
from app.api.v1.dependencies.subscription import check_subscription_tier
from app.core import meta_signature
from app.core.config import settings
from app.core.encryption import decrypt_pii
from app.core.rate_limiter import limiter
from app.core.rbac import RoleLevel
from app.db.base import get_db
from app.db.models.user import User
from app.schemas.marketing import (
    CampaignCreateRequest,
    CampaignOut,
    WhatsAppConnectRequest,
    WhatsAppEmbeddedSignupConfigOut,
    WhatsAppStatusOut,
    WhatsAppTemplateOut,
)
from app.services import campaign_service
from app.services.campaign_service import MARKETING_FEATURE

router = APIRouter(prefix="/marketing", tags=["marketing"])
webhook_router = APIRouter(tags=["marketing"])


# --- WhatsApp WABA connect / status / templates ------------------------------


@router.get(
    "/whatsapp/embedded-signup-config",
    response_model=WhatsAppEmbeddedSignupConfigOut,
    status_code=status.HTTP_200_OK,
)
@limiter.limit("30/minute")
async def whatsapp_embedded_signup_config(
    request: Request,
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    _plan_ok: User = Depends(check_subscription_tier(MARKETING_FEATURE)),
) -> WhatsAppEmbeddedSignupConfigOut:
    """What the dashboard's Embedded Signup JS SDK needs to launch the
    Facebook popup. 402s if the tenant's plan doesn't include WhatsApp marketing."""
    return WhatsAppEmbeddedSignupConfigOut(
        app_id=settings.META_APP_ID, config_id=settings.META_EMBEDDED_SIGNUP_CONFIG_ID
    )


def _status_out(account) -> WhatsAppStatusOut:  # noqa: ANN001
    return WhatsAppStatusOut(
        is_connected=account.is_connected,
        display_phone_number=decrypt_pii(account.encrypted_display_phone_number)
        if account.is_connected
        else None,
        quality_rating=account.quality_rating,
        messaging_tier=account.messaging_tier,
        webhook_subscribed=account.webhook_subscribed,
        connected_at=account.connected_at,
    )


@router.post("/whatsapp/connect", response_model=WhatsAppStatusOut, status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def connect_whatsapp(
    request: Request,
    payload: WhatsAppConnectRequest,
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    _plan_ok: User = Depends(check_subscription_tier(MARKETING_FEATURE)),
    session: AsyncSession = Depends(get_db),
) -> WhatsAppStatusOut:
    """Finish Embedded Signup for the caller's tenant. Owner+ only."""
    account = await campaign_service.connect_whatsapp_account(
        session,
        tenant_id=current_user.tenant_id,
        code=payload.code,
        waba_id=payload.waba_id,
        phone_number_id=payload.phone_number_id,
        business_id=payload.business_id,
    )
    return _status_out(account)


@router.get("/whatsapp/status", response_model=WhatsAppStatusOut, status_code=status.HTTP_200_OK)
async def whatsapp_status(
    current_user: User = Depends(require_role(RoleLevel.MANAGER)),
    session: AsyncSession = Depends(get_db),
) -> WhatsAppStatusOut:
    try:
        account = await campaign_service.get_connected_account(session, current_user.tenant_id)
    except campaign_service.WhatsAppNotConnected:
        return WhatsAppStatusOut(is_connected=False, webhook_subscribed=False)
    return _status_out(account)


@router.post("/whatsapp/disconnect", response_model=WhatsAppStatusOut, status_code=status.HTTP_200_OK)
@limiter.limit("10/minute")
async def disconnect_whatsapp(
    request: Request,
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    session: AsyncSession = Depends(get_db),
) -> WhatsAppStatusOut:
    account = await campaign_service.disconnect_whatsapp_account(session, current_user.tenant_id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "WHATSAPP_NOT_CONNECTED",
                    "message": "No connected WhatsApp Business Account for this tenant.",
                }
            },
        )
    return _status_out(account)


@router.get(
    "/whatsapp/templates", response_model=list[WhatsAppTemplateOut], status_code=status.HTTP_200_OK
)
async def list_whatsapp_templates(
    current_user: User = Depends(require_role(RoleLevel.MANAGER)),
    session: AsyncSession = Depends(get_db),
) -> list[WhatsAppTemplateOut]:
    templates = await campaign_service.list_templates(session, current_user.tenant_id)
    return [WhatsAppTemplateOut.model_validate(t) for t in templates]


@router.post(
    "/whatsapp/templates/sync",
    response_model=list[WhatsAppTemplateOut],
    status_code=status.HTTP_200_OK,
)
@limiter.limit("10/minute")
async def sync_whatsapp_templates(
    request: Request,
    current_user: User = Depends(require_role(RoleLevel.MANAGER)),
    session: AsyncSession = Depends(get_db),
) -> list[WhatsAppTemplateOut]:
    """Pull the tenant's current template list (and approval status) from Meta."""
    try:
        templates = await campaign_service.sync_templates(session, current_user.tenant_id)
    except campaign_service.WhatsAppNotConnected as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "WHATSAPP_NOT_CONNECTED",
                    "message": "Connect a WhatsApp Business Account before syncing templates.",
                }
            },
        ) from exc
    return [WhatsAppTemplateOut.model_validate(t) for t in templates]


# --- Campaigns --------------------------------------------------------------


@router.post("/campaigns", response_model=CampaignOut, status_code=status.HTTP_201_CREATED)
@limiter.limit("30/hour")
async def create_campaign(
    request: Request,
    payload: CampaignCreateRequest,
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    _plan_ok: User = Depends(check_subscription_tier(MARKETING_FEATURE)),
    session: AsyncSession = Depends(get_db),
) -> CampaignOut:
    """Create a draft campaign. Owner+ only. 402s if the tenant's plan
    doesn't include WhatsApp marketing."""
    try:
        campaign = await campaign_service.create_campaign(
            session,
            tenant_id=current_user.tenant_id,
            created_by_user_id=current_user.id,
            name=payload.name,
            template_id=payload.template_id,
            audience_filter=payload.audience_filter,
            branch_id=payload.branch_id,
        )
    except campaign_service.InvalidTemplate as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "INVALID_TEMPLATE",
                    "message": "Choose an approved MARKETING-category template.",
                }
            },
        ) from exc
    return CampaignOut.model_validate(campaign)


@router.get("/campaigns", response_model=list[CampaignOut], status_code=status.HTTP_200_OK)
async def list_campaigns(
    current_user: User = Depends(require_role(RoleLevel.MANAGER)),
    session: AsyncSession = Depends(get_db),
) -> list[CampaignOut]:
    campaigns = await campaign_service.list_campaigns(session, current_user.tenant_id)
    return [CampaignOut.model_validate(c) for c in campaigns]


@router.get("/campaigns/{campaign_id}", response_model=CampaignOut, status_code=status.HTTP_200_OK)
async def get_campaign(
    campaign_id: uuid.UUID,
    current_user: User = Depends(require_role(RoleLevel.MANAGER)),
    session: AsyncSession = Depends(get_db),
) -> CampaignOut:
    try:
        campaign = await campaign_service.get_campaign(session, current_user.tenant_id, campaign_id)
    except campaign_service.CampaignNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "CAMPAIGN_NOT_FOUND", "message": "No such campaign."}},
        ) from exc
    return CampaignOut.model_validate(campaign)


@router.post(
    "/campaigns/{campaign_id}/launch", response_model=CampaignOut, status_code=status.HTTP_200_OK
)
@limiter.limit("30/hour")
async def launch_campaign(
    request: Request,
    campaign_id: uuid.UUID,
    current_user: User = Depends(require_role(RoleLevel.OWNER)),
    _plan_ok: User = Depends(check_subscription_tier(MARKETING_FEATURE)),
    session: AsyncSession = Depends(get_db),
) -> CampaignOut:
    """Resolve the opted-in audience and hand the send off to Celery. Owner+ only."""
    try:
        campaign = await campaign_service.launch_campaign(session, current_user.tenant_id, campaign_id)
    except campaign_service.CampaignNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "CAMPAIGN_NOT_FOUND", "message": "No such campaign."}},
        ) from exc
    return CampaignOut.model_validate(campaign)


# --- Meta webhook (unauthenticated, HMAC-signed) -----------------------------


@webhook_router.get("/webhooks/whatsapp", status_code=status.HTTP_200_OK)
async def whatsapp_webhook_verify(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> Response:
    """Meta's one-time subscription handshake when a webhook URL is configured."""
    from app.services.whatsapp_service import verify_webhook_challenge  # noqa: PLC0415

    challenge = verify_webhook_challenge(hub_mode, hub_verify_token, hub_challenge)
    if challenge is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)
    return Response(content=challenge, media_type="text/plain")


@webhook_router.post("/webhooks/whatsapp", status_code=status.HTTP_200_OK)
@limiter.limit("300/minute")
async def whatsapp_webhook(request: Request) -> dict:
    # Raw bytes, read and verified before any parsing — same discipline as
    # the Razorpay webhook (billing.py).
    raw_body = await request.body()
    signature = request.headers.get(meta_signature.SIGNATURE_HEADER)
    meta_signature.verify_webhook_signature(raw_body, signature, settings.META_APP_SECRET)

    import json  # noqa: PLC0415

    from app.workers.tasks import process_whatsapp_webhook_event  # noqa: PLC0415

    payload = json.loads(raw_body)
    process_whatsapp_webhook_event.delay(payload)
    return {"status": "ok"}
