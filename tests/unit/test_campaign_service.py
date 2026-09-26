"""Unit tests for campaign_service — WhatsApp WABA connect, campaign
create/launch/send, and webhook-driven status/opt-out updates.

httpx and Meta calls (via whatsapp_service) are always mocked (AGENTS.md §7).
`rls.tenant_context`/`admin_bypass_context` are stubbed to no-ops throughout,
same rationale test_response_service.py documents: the RLS binding itself is
covered elsewhere, and pinning it here per call would only re-assert the SQL
text.
"""

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.encryption import decrypt_pii, encrypt_pii
from app.db.models.customer import Customer
from app.db.models.whatsapp import (
    CampaignRecipient,
    MarketingCampaign,
    WhatsAppBusinessAccount,
    WhatsAppTemplate,
)
from app.services import campaign_service
from app.services.whatsapp_service import WhatsAppOAuthError, WhatsAppSendError

TENANT_ID = uuid.uuid4()


@asynccontextmanager
async def _noop_context(*args, **kwargs):  # noqa: ARG001
    yield


@pytest.fixture(autouse=True)
def no_rls_context(mocker):
    mocker.patch("app.services.campaign_service.rls.tenant_context", _noop_context)
    mocker.patch("app.services.campaign_service.rls.admin_bypass_context", _noop_context)


def make_result(scalar=None, scalars_list=None, all_rows=None, first_value=None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar
    result.scalar_one.return_value = scalar
    result.scalars.return_value.all.return_value = scalars_list or []
    result.all.return_value = all_rows or []
    result.first.return_value = first_value
    return result


def make_session(results: list) -> MagicMock:
    """Each item is either a pre-built `make_result(...)` mock (for tests that
    need scalars()/all()/first()), or a bare model instance / None, which is
    wrapped as that call's `scalar_one_or_none()` value."""
    session = MagicMock()
    session.commit = AsyncMock()
    session.add = MagicMock()
    wrapped = [r if isinstance(r, MagicMock) else make_result(scalar=r) for r in results]
    session.execute = AsyncMock(side_effect=wrapped)
    return session


def make_account(**overrides) -> WhatsAppBusinessAccount:
    defaults = {
        "tenant_id": TENANT_ID,
        "waba_id": "waba-1",
        "phone_number_id": "phone-1",
        "business_id": "biz-1",
        "phone_hash": "hash",
        "encrypted_display_phone_number": encrypt_pii("+919000000000"),
        "encrypted_access_token": encrypt_pii("meta-access-token"),
        "token_exchanged_at": datetime.now(timezone.utc),
        "is_connected": True,
        "webhook_subscribed": True,
        "connected_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    account = WhatsAppBusinessAccount(**defaults)
    account.id = uuid.uuid4()
    return account


def make_template(**overrides) -> WhatsAppTemplate:
    defaults = {
        "tenant_id": TENANT_ID,
        "waba_account_id": uuid.uuid4(),
        "meta_template_id": "meta-1",
        "name": "promo_offer",
        "language": "en",
        "category": "MARKETING",
        "status": "APPROVED",
        "components": [],
        "last_synced_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    template = WhatsAppTemplate(**defaults)
    template.id = uuid.uuid4()
    return template


def make_campaign(**overrides) -> MarketingCampaign:
    defaults = {
        "tenant_id": TENANT_ID,
        "branch_id": None,
        "name": "Weekend offer",
        "template_id": uuid.uuid4(),
        "audience_filter": {},
        "created_by_user_id": uuid.uuid4(),
        "status": "draft",
        "recipients_total": 0,
        "sent_count": 0,
        "delivered_count": 0,
        "read_count": 0,
        "failed_count": 0,
    }
    defaults.update(overrides)
    campaign = MarketingCampaign(**defaults)
    campaign.id = uuid.uuid4()
    return campaign


def make_customer(**overrides) -> Customer:
    defaults = {
        "tenant_id": TENANT_ID,
        "phone_hash": "phonehash",
        "encrypted_phone": encrypt_pii("+919876543210"),
        "whatsapp_marketing_opt_in": True,
        "is_blocked": False,
        "total_stamps_alltime": 0,
        "current_reward_count": 0,
    }
    defaults.update(overrides)
    customer = Customer(**defaults)
    customer.id = uuid.uuid4()
    return customer


def make_recipient(**overrides) -> CampaignRecipient:
    defaults = {
        "tenant_id": TENANT_ID,
        "campaign_id": uuid.uuid4(),
        "customer_id": uuid.uuid4(),
        "status": "queued",
    }
    defaults.update(overrides)
    recipient = CampaignRecipient(**defaults)
    recipient.id = uuid.uuid4()
    return recipient


# --- connect / disconnect / get_connected_account ---------------------------


@pytest.mark.asyncio
async def test_connect_creates_a_new_account(mocker):
    mocker.patch(
        "app.services.campaign_service.whatsapp_service.exchange_code_for_token",
        AsyncMock(return_value={"access_token": "short-lived"}),
    )
    mocker.patch(
        "app.services.campaign_service.whatsapp_service.exchange_for_long_lived_token",
        AsyncMock(return_value={"access_token": "long-lived-token"}),
    )
    mocker.patch(
        "app.services.campaign_service.whatsapp_service.fetch_phone_number_details",
        AsyncMock(
            return_value={
                "display_phone_number": "+919000000000",
                "quality_rating": "GREEN",
                "messaging_limit_tier": "TIER_1K",
            }
        ),
    )
    mocker.patch(
        "app.services.campaign_service.whatsapp_service.subscribe_app_to_waba", AsyncMock()
    )
    session = make_session([None])  # no existing account

    account = await campaign_service.connect_whatsapp_account(
        session,
        tenant_id=TENANT_ID,
        code="the-code",
        waba_id="waba-1",
        phone_number_id="phone-1",
        business_id="biz-1",
    )

    assert account.is_connected is True
    assert account.webhook_subscribed is True
    assert decrypt_pii(account.encrypted_access_token) == "long-lived-token"
    assert account.quality_rating == "GREEN"
    session.add.assert_called_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_connect_survives_a_webhook_subscribe_failure(mocker):
    mocker.patch(
        "app.services.campaign_service.whatsapp_service.exchange_code_for_token",
        AsyncMock(return_value={"access_token": "short-lived"}),
    )
    mocker.patch(
        "app.services.campaign_service.whatsapp_service.exchange_for_long_lived_token",
        AsyncMock(return_value={"access_token": "long-lived-token"}),
    )
    mocker.patch(
        "app.services.campaign_service.whatsapp_service.fetch_phone_number_details",
        AsyncMock(return_value={"display_phone_number": "+919000000000"}),
    )
    mocker.patch(
        "app.services.campaign_service.whatsapp_service.subscribe_app_to_waba",
        AsyncMock(side_effect=WhatsAppOAuthError("boom")),
    )
    session = make_session([None])

    account = await campaign_service.connect_whatsapp_account(
        session,
        tenant_id=TENANT_ID,
        code="the-code",
        waba_id="waba-1",
        phone_number_id="phone-1",
        business_id="biz-1",
    )

    assert account.is_connected is True
    assert account.webhook_subscribed is False


@pytest.mark.asyncio
async def test_disconnect_wipes_the_token():
    account = make_account()
    session = make_session([account])

    result = await campaign_service.disconnect_whatsapp_account(session, TENANT_ID)

    assert result is account
    assert account.is_connected is False
    assert account.encrypted_access_token == ""
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_disconnect_missing_account_returns_none():
    session = make_session([None])

    result = await campaign_service.disconnect_whatsapp_account(session, TENANT_ID)

    assert result is None
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_connected_account_raises_when_none():
    session = make_session([None])

    with pytest.raises(campaign_service.WhatsAppNotConnected):
        await campaign_service.get_connected_account(session, TENANT_ID)


# --- create_campaign ---------------------------------------------------------


@pytest.mark.asyncio
async def test_create_campaign_succeeds_for_an_approved_marketing_template():
    template = make_template(category="MARKETING", status="APPROVED")
    session = make_session([template])

    campaign = await campaign_service.create_campaign(
        session,
        tenant_id=TENANT_ID,
        created_by_user_id=uuid.uuid4(),
        name="Weekend offer",
        template_id=template.id,
        audience_filter={},
    )

    assert campaign.status == "draft"
    session.add.assert_called_once()
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_campaign_rejects_a_utility_template():
    template = make_template(category="UTILITY", status="APPROVED")
    session = make_session([template])

    with pytest.raises(campaign_service.InvalidTemplate):
        await campaign_service.create_campaign(
            session,
            tenant_id=TENANT_ID,
            created_by_user_id=uuid.uuid4(),
            name="Weekend offer",
            template_id=template.id,
            audience_filter={},
        )


@pytest.mark.asyncio
async def test_create_campaign_rejects_a_pending_template():
    template = make_template(category="MARKETING", status="PENDING")
    session = make_session([template])

    with pytest.raises(campaign_service.InvalidTemplate):
        await campaign_service.create_campaign(
            session,
            tenant_id=TENANT_ID,
            created_by_user_id=uuid.uuid4(),
            name="Weekend offer",
            template_id=template.id,
            audience_filter={},
        )


@pytest.mark.asyncio
async def test_create_campaign_rejects_an_unknown_template():
    session = make_session([None])

    with pytest.raises(campaign_service.InvalidTemplate):
        await campaign_service.create_campaign(
            session,
            tenant_id=TENANT_ID,
            created_by_user_id=uuid.uuid4(),
            name="Weekend offer",
            template_id=uuid.uuid4(),
            audience_filter={},
        )


# --- launch_campaign ----------------------------------------------------------


@pytest.mark.asyncio
async def test_launch_campaign_creates_one_recipient_per_opted_in_customer(mocker):
    campaign = make_campaign(status="draft")
    customers = [make_customer(), make_customer(), make_customer()]
    session = make_session([make_result(scalar=campaign), make_result(scalars_list=customers)])
    send_task = mocker.patch("app.workers.tasks.send_campaign_task")

    result = await campaign_service.launch_campaign(session, TENANT_ID, campaign.id)

    assert result.recipients_total == 3
    assert result.status == "sending"
    assert session.add.call_count == 3
    session.commit.assert_awaited_once()
    send_task.delay.assert_called_once_with(str(campaign.id))


@pytest.mark.asyncio
async def test_launch_campaign_is_a_no_op_when_not_a_draft(mocker):
    campaign = make_campaign(status="completed")
    session = make_session([make_result(scalar=campaign)])
    send_task = mocker.patch("app.workers.tasks.send_campaign_task")

    result = await campaign_service.launch_campaign(session, TENANT_ID, campaign.id)

    assert result is campaign
    session.add.assert_not_called()
    send_task.delay.assert_not_called()


# --- send_campaign_batch -------------------------------------------------------


@pytest.mark.asyncio
async def test_send_campaign_batch_sends_every_queued_recipient(mocker):
    mocker.patch(
        "app.services.campaign_service._resolve_campaign_tenant", AsyncMock(return_value=TENANT_ID)
    )
    campaign = make_campaign(status="sending")
    account = make_account()
    template = make_template()
    customer1, customer2 = make_customer(), make_customer()
    recipient1 = make_recipient(campaign_id=campaign.id, customer_id=customer1.id)
    recipient2 = make_recipient(campaign_id=campaign.id, customer_id=customer2.id)
    session = make_session(
        [
            make_result(scalar=campaign),
            make_result(scalar=account),
            make_result(scalar=template),
            make_result(all_rows=[(recipient1, customer1), (recipient2, customer2)]),
            make_result(first_value=None),  # nothing left queued
        ]
    )
    mocker.patch(
        "app.services.campaign_service.whatsapp_service.send_template_message",
        AsyncMock(side_effect=["wamid.1", "wamid.2"]),
    )

    sent = await campaign_service.send_campaign_batch(session, campaign.id)

    assert sent == 2
    assert recipient1.status == "sent"
    assert recipient2.status == "sent"
    assert campaign.sent_count == 2
    assert campaign.status == "completed"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_campaign_batch_stops_on_a_rate_limit_error(mocker):
    mocker.patch(
        "app.services.campaign_service._resolve_campaign_tenant", AsyncMock(return_value=TENANT_ID)
    )
    campaign = make_campaign(status="sending")
    account = make_account()
    template = make_template()
    customer1, customer2 = make_customer(), make_customer()
    recipient1 = make_recipient(campaign_id=campaign.id, customer_id=customer1.id)
    recipient2 = make_recipient(campaign_id=campaign.id, customer_id=customer2.id)
    session = make_session(
        [
            make_result(scalar=campaign),
            make_result(scalar=account),
            make_result(scalar=template),
            make_result(all_rows=[(recipient1, customer1), (recipient2, customer2)]),
            make_result(first_value=recipient2.id),  # still one queued
        ]
    )
    mocker.patch(
        "app.services.campaign_service.whatsapp_service.send_template_message",
        AsyncMock(side_effect=WhatsAppSendError("rate limited", error_code="131056")),
    )

    sent = await campaign_service.send_campaign_batch(session, campaign.id)

    assert sent == 0
    assert recipient1.status == "queued"  # untouched — will retry next run
    assert campaign.status == "sending"  # not completed, recipients remain


@pytest.mark.asyncio
async def test_send_campaign_batch_marks_a_non_rate_limit_failure(mocker):
    mocker.patch(
        "app.services.campaign_service._resolve_campaign_tenant", AsyncMock(return_value=TENANT_ID)
    )
    campaign = make_campaign(status="sending")
    account = make_account()
    template = make_template()
    customer = make_customer()
    recipient = make_recipient(campaign_id=campaign.id, customer_id=customer.id)
    session = make_session(
        [
            make_result(scalar=campaign),
            make_result(scalar=account),
            make_result(scalar=template),
            make_result(all_rows=[(recipient, customer)]),
            make_result(first_value=None),
        ]
    )
    mocker.patch(
        "app.services.campaign_service.whatsapp_service.send_template_message",
        AsyncMock(side_effect=WhatsAppSendError("invalid number", error_code="131026")),
    )

    sent = await campaign_service.send_campaign_batch(session, campaign.id)

    assert sent == 0
    assert recipient.status == "failed"
    assert recipient.error_code == "131026"
    assert campaign.failed_count == 1


@pytest.mark.asyncio
async def test_send_campaign_batch_returns_zero_for_an_unresolvable_tenant(mocker):
    mocker.patch(
        "app.services.campaign_service._resolve_campaign_tenant", AsyncMock(return_value=None)
    )
    session = make_session([])

    sent = await campaign_service.send_campaign_batch(session, uuid.uuid4())

    assert sent == 0
    session.execute.assert_not_awaited()


# --- webhook-driven status updates --------------------------------------------


@pytest.mark.asyncio
async def test_apply_delivery_status_marks_delivered_and_increments_campaign():
    recipient = make_recipient(status="sent")
    campaign = make_campaign(delivered_count=0)
    session = make_session([recipient, campaign])

    await campaign_service.apply_delivery_status(
        session, "wamid.1", "delivered", datetime.now(timezone.utc)
    )

    assert recipient.status == "delivered"
    assert campaign.delivered_count == 1
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_delivery_status_marks_read_and_increments_campaign():
    recipient = make_recipient(status="delivered")
    campaign = make_campaign(read_count=0)
    session = make_session([recipient, campaign])

    await campaign_service.apply_delivery_status(session, "wamid.1", "read", datetime.now(timezone.utc))

    assert recipient.status == "read"
    assert campaign.read_count == 1


@pytest.mark.asyncio
async def test_apply_delivery_status_is_a_no_op_for_an_unknown_message_id():
    session = make_session([None])

    await campaign_service.apply_delivery_status(
        session, "wamid.unknown", "delivered", datetime.now(timezone.utc)
    )

    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_apply_opt_out_flips_the_marketing_flag_only():
    customer = make_customer(whatsapp_marketing_opt_in=True, whatsapp_opt_in=True)
    session = make_session([customer])

    await campaign_service.apply_opt_out(session, TENANT_ID, "+919876543210")

    assert customer.whatsapp_marketing_opt_in is False
    assert customer.whatsapp_opt_in is True  # transactional consent untouched
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_apply_opt_out_is_a_no_op_for_an_unknown_phone():
    session = make_session([None])

    await campaign_service.apply_opt_out(session, TENANT_ID, "+910000000000")

    session.commit.assert_not_awaited()


# --- _is_opt_out_message (pure) ------------------------------------------------


def test_is_opt_out_message_matches_stop_text():
    assert campaign_service._is_opt_out_message({"type": "text", "text": {"body": "STOP"}}) is True


def test_is_opt_out_message_ignores_ordinary_text():
    assert (
        campaign_service._is_opt_out_message({"type": "text", "text": {"body": "sounds great!"}})
        is False
    )


def test_is_opt_out_message_matches_the_opt_out_button():
    assert (
        campaign_service._is_opt_out_message({"type": "button", "button": {"text": "Stop"}}) is True
    )


# --- process_webhook_payload ---------------------------------------------------


@pytest.mark.asyncio
async def test_process_webhook_payload_routes_a_status_update(mocker):
    mocker.patch(
        "app.services.campaign_service._resolve_tenant_by_phone_number_id",
        AsyncMock(return_value=TENANT_ID),
    )
    apply_status = mocker.patch(
        "app.services.campaign_service.apply_delivery_status", AsyncMock()
    )
    mocker.patch("app.services.campaign_service.apply_opt_out", AsyncMock())
    session = make_session([])

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "phone-1"},
                            "statuses": [
                                {"id": "wamid.1", "status": "delivered", "timestamp": "1700000000"}
                            ],
                        }
                    }
                ]
            }
        ]
    }

    await campaign_service.process_webhook_payload(session, payload)

    apply_status.assert_awaited_once()
    args = apply_status.await_args.args
    assert args[1] == "wamid.1"
    assert args[2] == "delivered"


@pytest.mark.asyncio
async def test_process_webhook_payload_routes_an_opt_out(mocker):
    mocker.patch(
        "app.services.campaign_service._resolve_tenant_by_phone_number_id",
        AsyncMock(return_value=TENANT_ID),
    )
    mocker.patch("app.services.campaign_service.apply_delivery_status", AsyncMock())
    apply_opt_out = mocker.patch("app.services.campaign_service.apply_opt_out", AsyncMock())
    session = make_session([])

    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "metadata": {"phone_number_id": "phone-1"},
                            "messages": [
                                {"type": "text", "from": "+919876543210", "text": {"body": "stop"}}
                            ],
                        }
                    }
                ]
            }
        ]
    }

    await campaign_service.process_webhook_payload(session, payload)

    apply_opt_out.assert_awaited_once_with(session, TENANT_ID, "+919876543210")


@pytest.mark.asyncio
async def test_process_webhook_payload_ignores_an_unresolvable_phone_number(mocker):
    mocker.patch(
        "app.services.campaign_service._resolve_tenant_by_phone_number_id",
        AsyncMock(return_value=None),
    )
    apply_status = mocker.patch("app.services.campaign_service.apply_delivery_status", AsyncMock())
    apply_opt_out = mocker.patch("app.services.campaign_service.apply_opt_out", AsyncMock())
    session = make_session([])

    payload = {
        "entry": [
            {"changes": [{"value": {"metadata": {"phone_number_id": "unknown-phone"}, "statuses": []}}]}
        ]
    }

    await campaign_service.process_webhook_payload(session, payload)

    apply_status.assert_not_awaited()
    apply_opt_out.assert_not_awaited()
