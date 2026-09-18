"""QuickBite — Cloudflare R2 object storage client (NICE-04).

R2 is S3-compatible, so `boto3`'s S3 client works against it unmodified with
just a custom `endpoint_url`. This is the first place in the codebase that
actually uploads to R2 — `qr_service.py`'s docstring explicitly deferred it
("R2 upload is Doc 2's documented storage target... but is not wired here")
for QR PNGs, which stay on-demand-generated; exports are different because
the whole point is a durable link the owner can revisit for 24h without the
export having to be regenerated on every click.

The client requires `R2_ACCOUNT_ID`/`R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY`
to be configured — all default to `""`, so a dev/CI box with no R2 project
gets a clear `RuntimeError` at call time rather than a confusing boto3
connection failure against an empty endpoint URL.
"""

import uuid
from datetime import timedelta

import boto3
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

# Doc 5 NICE-04: "URL expires after 24 hours (R2 presigned URL TTL)".
PRESIGNED_URL_TTL_SECONDS = int(timedelta(hours=24).total_seconds())
# Doc 5 NICE-04: "R2 object auto-deleted after 7 days" — this is a bucket
# lifecycle rule configured in Cloudflare's dashboard/API (infra, not
# something an upload call can set per-object), tracked here as the value
# that lifecycle rule must use so the two stay in sync if either changes.
OBJECT_RETENTION_DAYS = 7


def _client():
    if not (settings.R2_ACCOUNT_ID and settings.R2_ACCESS_KEY_ID and settings.R2_SECRET_ACCESS_KEY):
        raise RuntimeError(
            "R2_ACCOUNT_ID/R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY must be configured "
            "before uploading to R2 storage."
        )
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",  # R2 ignores region but boto3 requires one
    )


def upload_and_presign(key: str, body: bytes, content_type: str) -> str:
    """Uploads `body` to `key` and returns a presigned GET URL valid for
    `PRESIGNED_URL_TTL_SECONDS`. Synchronous (boto3 has no native asyncio
    client) — callers are Celery tasks, which already run outside the
    request event loop, so this never blocks a FastAPI request handler."""
    client = _client()
    client.put_object(
        Bucket=settings.R2_BUCKET_NAME, Key=key, Body=body, ContentType=content_type
    )
    url = client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.R2_BUCKET_NAME, "Key": key},
        ExpiresIn=PRESIGNED_URL_TTL_SECONDS,
    )
    logger.info("storage.r2.uploaded", key=key, content_type=content_type)
    return url


def export_object_key(tenant_id: uuid.UUID, export_format: str) -> str:
    """`exports/{tenant_id}/{uuid}.{ext}` — namespaced per tenant so a bucket
    listing (or a lifecycle rule scoped by prefix, if ever needed) can reason
    about one tenant's exports without touching another's."""
    return f"exports/{tenant_id}/{uuid.uuid4()}.{export_format}"
