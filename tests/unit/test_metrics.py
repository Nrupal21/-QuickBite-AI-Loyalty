"""Unit tests for SEC-29's Prometheus counters — confirms the exact call
sites documented in app/core/metrics.py actually increment, and that
/metrics renders a scrapeable body."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.core import metrics
from app.core.encryption import sha256_hex
from app.db.models.customer import Customer
from app.main import app
from app.schemas.customer_auth import OTPVerify


def _counter_value(counter) -> float:
    return counter._value.get()  # noqa: SLF001 — prometheus_client's own test pattern


@pytest.mark.asyncio
async def test_otp_too_many_attempts_increments_verify_failure_counter(mocker):
    from app.services import customer_otp_service

    tenant_id = uuid.uuid4()
    customer = Customer(
        tenant_id=tenant_id, phone_hash=sha256_hex("+919876543210"), otp_attempts=2
    )
    customer.id = uuid.uuid4()
    result = MagicMock()
    result.scalar_one_or_none.return_value = customer
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()

    mocker.patch("app.services.customer_otp_service.rls.set_tenant_context", AsyncMock())
    mocker.patch(
        "app.services.customer_otp_service.cache_service.get", AsyncMock(return_value="correct-hash")
    )
    mocker.patch("app.services.customer_otp_service.cache_service.delete", AsyncMock())

    before = _counter_value(
        metrics.OTP_VERIFY_FAILURE_TOTAL.labels(reason="too_many_attempts")
    )

    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        await customer_otp_service.verify_otp(
            OTPVerify(identifier="+919876543210", tenant_id=tenant_id, otp_code="000000"), session
        )

    after = _counter_value(metrics.OTP_VERIFY_FAILURE_TOTAL.labels(reason="too_many_attempts"))
    assert after == before + 1


@pytest.mark.asyncio
async def test_metrics_endpoint_exposes_registered_counters():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert "quickbite_otp_verify_failure_total" in response.text
    assert "quickbite_jwt_revocations_total" in response.text
    assert "quickbite_fraud_scan_total" in response.text
    assert "quickbite_otp_delivery_failure_total" in response.text
