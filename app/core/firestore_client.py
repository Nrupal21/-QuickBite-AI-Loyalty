"""Lazy async Firestore client for the payment projection mirror.

Built directly from `google.cloud.firestore.AsyncClient` and the same
service-account JSON already validated in `app/core/config.py`, independent of
the `firebase_admin` App used for ID-token verification (`firebase_auth.py`).
The two are unrelated `firebase_admin` never exposes an async Firestore client
in the Python SDK, so going straight to `google-cloud-firestore` (already a
transitive dependency of `firebase-admin`) avoids leaning on undocumented
internals of a different SDK surface.
"""

import json
import threading
from typing import Any

import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

_client: Any = None
_init_lock = threading.Lock()


class FirestoreNotConfiguredError(RuntimeError):
    """Raised when the projection worker runs without Firebase credentials."""


def get_client() -> Any:
    """The process-wide AsyncClient, constructed on first use.

    Lazy for the same reason as firebase_auth.get_app(): settings default
    every Firebase field to empty, so an unconfigured environment (every test
    run) must never attempt to build one.
    """
    global _client  # noqa: PLW0603
    if _client is not None:
        return _client
    with _init_lock:
        if _client is None:
            if not settings.firebase_enabled:
                msg = "FIREBASE_PROJECT_ID / FIREBASE_SERVICE_ACCOUNT_JSON are not set"
                raise FirestoreNotConfiguredError(msg)
            from google.cloud.firestore import AsyncClient  # noqa: PLC0415
            from google.oauth2 import service_account  # noqa: PLC0415

            info = json.loads(settings.FIREBASE_SERVICE_ACCOUNT_JSON)
            credentials = service_account.Credentials.from_service_account_info(info)
            _client = AsyncClient(
                project=settings.FIREBASE_PROJECT_ID,
                credentials=credentials,
                database=settings.FIRESTORE_DATABASE,
            )
            logger.info("billing.firestore.initialised", project_id=settings.FIREBASE_PROJECT_ID)
    return _client


def reset_client() -> None:
    """Drop the cached client. For tests only."""
    global _client  # noqa: PLW0603
    _client = None
