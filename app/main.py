"""QuickBite AI + Loyalty — FastAPI Application Entry Point.

Creates the FastAPI app, configures structlog JSON logging,
and registers the health endpoint.
"""

import structlog
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api.v1 import api_router
from app.api.v1.routers.pages import router as pages_router
from app.core.config import settings
from app.core.rate_limiter import limiter

# sentry-sdk is prod-only (requirements/prod.txt) — imported lazily so dev/test
# environments without it installed are unaffected when SENTRY_DSN is unset.
if settings.SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
        # PII is never sent to Sentry — matches the app-wide no-PII-in-logs rule.
        send_default_pii=False,
    )

# Structlog configuration — JSON output with timestamps and context
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(0),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

log = structlog.get_logger()

app = FastAPI(
    title="QuickBite AI + Loyalty",
    description="Multi-tenant SaaS for restaurant reputation management and QR loyalty rewards",
    version="0.1.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.include_router(api_router)
app.include_router(pages_router)  # server-rendered pages, no /api/v1 prefix
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
async def startup_event() -> None:
    """Log application startup. Config import validates env vars.

    INFRA-02: verifies PostGIS is enabled on the database and logs its
    version — geofencing cannot work without it, so startup fails hard.
    """
    from sqlalchemy import text

    from app.db.base import engine

    log.info(
        "quickbite_startup",
        environment=settings.ENVIRONMENT,
        debug=settings.DEBUG,
    )

    if settings.POSTGIS_ENABLED:
        try:
            async with engine.connect() as conn:
                version = (await conn.execute(text("SELECT PostGIS_Version()"))).scalar_one()
        except Exception as exc:
            log.error("postgis_verification_failed", error=str(exc))
            raise RuntimeError(
                "PostGIS verification failed — geofencing requires PostGIS 3.4"
            ) from exc
        log.info("postgis_enabled", version=version)


@app.get("/health/live", response_class=JSONResponse)
async def health_live() -> dict[str, str]:
    """Liveness probe — returns 200 if the app is running."""
    return {"status": "ok"}
