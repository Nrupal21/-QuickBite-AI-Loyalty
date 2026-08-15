"""Firebase Auth ID-token verification.

`firebase_admin.auth.verify_id_token` is synchronous and, on a cold public-key
cache, does network I/O to Google. Calling it directly from an `async def`
route would block the event loop for the duration — so it runs in a worker
thread, bounded so that a Google outage cannot take the rest of the app down
with it.

Initialisation is lazy rather than at import: `app/core/config.py` builds
`settings` at import time with every Firebase field defaulted to empty, so an
unconfigured environment (every test run, most dev boxes) must not attempt to
parse credentials. `warm_up()` exists for `main.py` to pay the cold-start cost
at boot instead of on a user's first request.
"""

import functools
import json
import threading
from typing import Any

import anyio.to_thread
import structlog
from fastapi import HTTPException, status

from app.core.config import settings

logger = structlog.get_logger(__name__)

# Named so we never collide with a default app another library might create.
_APP_NAME = "quickbite-auth"
# Bounds the HTTP call firebase-admin makes for Google's signing certs. The
# library default is generous enough that a hung connection would pin a worker
# thread for minutes.
_HTTP_TIMEOUT_SECONDS = 5
# Caps how many threads Firebase verification can occupy at once. anyio's
# default pool is 40 and is shared with passlib's bcrypt (rounds=12) — without
# this ceiling, a Firebase stall would starve password logins of threads.
_MAX_CONCURRENT_VERIFICATIONS = 20
_CLOCK_SKEW_SECONDS = 30

_app: Any = None
# threading.Lock, not asyncio.Lock: initialize_app is synchronous and may be
# entered from a worker thread, where an asyncio primitive has no meaning.
_init_lock = threading.Lock()
_verify_slots = anyio.Semaphore(_MAX_CONCURRENT_VERIFICATIONS)

_INVALID = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail={
        "error": {
            "code": "INVALID_TOKEN",
            "message": "Your session has expired. Please log in again.",
        }
    },
)

_UNAVAILABLE = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail={
        "error": {
            "code": "AUTH_PROVIDER_UNAVAILABLE",
            "message": "Sign-in is temporarily unavailable. Please try again shortly.",
        }
    },
)


class FirebaseNotConfiguredError(RuntimeError):
    """Raised when Firebase is used without credentials — a deployment bug."""


def get_app() -> Any:
    """The initialised firebase_admin App, creating it on first use."""
    global _app  # noqa: PLW0603 - one process-wide SDK handle by design
    if _app is not None:
        return _app
    with _init_lock:
        if _app is None:
            if not settings.firebase_enabled:
                msg = "FIREBASE_PROJECT_ID / FIREBASE_SERVICE_ACCOUNT_JSON are not set"
                raise FirebaseNotConfiguredError(msg)
            # Imported here, not at module scope: firebase-admin is a heavy
            # optional dependency and importing it would make every test run
            # pay for a package this deployment may never use.
            import firebase_admin  # noqa: PLC0415
            from firebase_admin import credentials  # noqa: PLC0415

            cred = credentials.Certificate(json.loads(settings.FIREBASE_SERVICE_ACCOUNT_JSON))
            _app = firebase_admin.initialize_app(
                cred, options={"httpTimeout": _HTTP_TIMEOUT_SECONDS}, name=_APP_NAME
            )
            logger.info("auth.firebase.initialised", project_id=settings.FIREBASE_PROJECT_ID)
    return _app


def _verify_sync(token: str) -> dict[str, Any]:
    """Blocking verification — always call via `verify_firebase_token`.

    check_revoked=False on purpose: True adds a round trip to Firebase's user
    records API on *every* request. Revocation is handled locally instead by
    the `tokens_valid_from` watermark on the user/customer row, which costs no
    extra query because the row is already loaded.
    """
    from firebase_admin import auth as fb_auth  # noqa: PLC0415

    return fb_auth.verify_id_token(
        token,
        app=get_app(),
        check_revoked=False,
        clock_skew_seconds=_CLOCK_SKEW_SECONDS,
    )


async def verify_firebase_token(token: str) -> dict[str, Any]:
    """Verify a Firebase ID token and return its claims.

    Raises 401 for an invalid token, 503 when Firebase itself is unreachable or
    misconfigured — the same distinction the JWKS cache draws, and for the same
    reason: an outage must not read as "your session ended".
    """
    try:
        from firebase_admin import exceptions as fb_exceptions  # noqa: PLC0415
        from firebase_admin.auth import (  # noqa: PLC0415
            CertificateFetchError,
            InvalidIdTokenError,
        )
    except ImportError as exc:  # pragma: no cover - package missing from the image
        logger.error("auth.firebase.sdk_missing", error=str(exc))
        raise _UNAVAILABLE from exc

    try:
        async with _verify_slots:
            return await anyio.to_thread.run_sync(functools.partial(_verify_sync, token))
    except (InvalidIdTokenError, ValueError) as exc:
        # ValueError covers a structurally malformed token — firebase-admin
        # raises it before ever reaching signature checks.
        logger.info("auth.firebase.token_rejected", reason=type(exc).__name__)
        raise _INVALID from exc
    except (CertificateFetchError, FirebaseNotConfiguredError, fb_exceptions.FirebaseError) as exc:
        logger.error("auth.firebase.unavailable", error=str(exc))
        raise _UNAVAILABLE from exc


def warm_up() -> None:
    """Initialise the SDK at boot rather than on a user's first request. Blocking.

    Only constructs the app — firebase-admin fetches Google's signing certs
    lazily inside `verify_id_token`, and there is no public API to force that
    without a real token. So this removes the credential-parsing and HTTP
    client setup from the request path, not the first cert fetch.
    """
    app = get_app()
    logger.info("auth.firebase.warm", app=app.name)
