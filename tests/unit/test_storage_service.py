"""Unit tests for the R2 storage client (NICE-04). boto3 is always mocked,
per AGENTS.md's testing rules for external services — no real R2 project is
reachable from CI/this suite."""

import uuid
from unittest.mock import MagicMock

import pytest

from app.core.config import settings
from app.services import storage_service


def test_client_raises_without_r2_credentials(mocker):
    mocker.patch.object(settings, "R2_ACCOUNT_ID", "")
    mocker.patch.object(settings, "R2_ACCESS_KEY_ID", "")
    mocker.patch.object(settings, "R2_SECRET_ACCESS_KEY", "")

    with pytest.raises(RuntimeError, match="R2_ACCOUNT_ID"):
        storage_service._client()


def test_upload_and_presign_uploads_and_returns_presigned_url(mocker):
    mocker.patch.object(settings, "R2_ACCOUNT_ID", "acct123")
    mocker.patch.object(settings, "R2_ACCESS_KEY_ID", "key123")
    mocker.patch.object(settings, "R2_SECRET_ACCESS_KEY", "secret123")
    mocker.patch.object(settings, "R2_BUCKET_NAME", "quickbite-test")

    fake_client = MagicMock()
    fake_client.generate_presigned_url.return_value = "https://r2.example/signed-url"
    mocker.patch("app.services.storage_service.boto3.client", return_value=fake_client)

    url = storage_service.upload_and_presign("exports/tenant/file.csv", b"a,b,c", "text/csv")

    assert url == "https://r2.example/signed-url"
    fake_client.put_object.assert_called_once_with(
        Bucket="quickbite-test", Key="exports/tenant/file.csv", Body=b"a,b,c", ContentType="text/csv"
    )
    fake_client.generate_presigned_url.assert_called_once()
    call_kwargs = fake_client.generate_presigned_url.call_args.kwargs
    assert call_kwargs["ExpiresIn"] == storage_service.PRESIGNED_URL_TTL_SECONDS
    assert call_kwargs["Params"]["Key"] == "exports/tenant/file.csv"


def test_export_object_key_is_namespaced_per_tenant():
    tenant_id = uuid.uuid4()

    key = storage_service.export_object_key(tenant_id, "pdf")

    assert key.startswith(f"exports/{tenant_id}/")
    assert key.endswith(".pdf")
