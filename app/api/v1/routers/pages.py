"""QuickBite — Server-rendered pages (Jinja2), mounted WITHOUT the /api/v1 prefix.

Landing page (STITCH-12) lives here. Customer flow pages (scan, loyalty card,
OTP screens) join as their STITCH tickets land.

The dashboard pages (STITCH-07/09) are served with no server-side auth check:
they are static app shells with no tenant data baked in server-side — every
number on them comes from a client-side fetch to the JSON API using the
sessionStorage token auth-login.js already stores. An anonymous visitor can
load the *shell*, but /api/v1/dashboard/stats and /api/v1/loyalty/analytics
both require a valid bearer token, so nothing tenant-specific is ever
reachable without one. static/js/dashboard.js redirects to /login client-side
when no session is present.
"""

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.base import get_db
from app.services.auth_service import AuthService

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
async def landing_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "landing/index.html")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    """Unified identify-first login screen; tenant_id is a query param until TENANT-01 lands."""
    firebase_web_config_json = "null"
    if settings.firebase_web_configured:
        # Public web config only — never FIREBASE_SERVICE_ACCOUNT_JSON, which
        # is the server's own credential for verifying tokens.
        firebase_web_config_json = json.dumps(
            {
                "apiKey": settings.FIREBASE_WEB_API_KEY,
                "authDomain": settings.FIREBASE_WEB_AUTH_DOMAIN,
                "projectId": settings.FIREBASE_PROJECT_ID,
                "appId": settings.FIREBASE_WEB_APP_ID,
            }
        )
    return templates.TemplateResponse(
        request,
        "customer/login.html",
        {
            "firebase_web_config_json": firebase_web_config_json,
            "firebase_web_configured": settings.firebase_web_configured,
        },
    )


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request) -> HTMLResponse:
    """New-restaurant sign-up screen. AUTH-01's POST /api/v1/auth/register
    existed with no browser-facing path to it — every landing-page CTA led
    to /login, which has no way to create an account."""
    return templates.TemplateResponse(request, "customer/register.html")


@router.get("/verify-email", response_class=HTMLResponse)
async def verify_email_page(
    request: Request,
    token: str,
    session: AsyncSession = Depends(get_db),
) -> HTMLResponse:
    """Renders the link from the verification email. Calls the same
    AuthService.verify_email() as the JSON API (kept at
    /api/v1/auth/verify-email for programmatic callers) rather than
    duplicating it — a human clicking an email link needs a rendered page,
    not a bare JSON body."""
    try:
        result = await AuthService(session=session).verify_email(token)
    except HTTPException as exc:
        detail = exc.detail.get("error", {}) if isinstance(exc.detail, dict) else {}
        return templates.TemplateResponse(
            request,
            "customer/verify_email.html",
            {
                "success": False,
                "message": detail.get(
                    "message", "This verification link is invalid or has expired."
                ),
            },
            status_code=exc.status_code,
        )
    return templates.TemplateResponse(
        request,
        "customer/verify_email.html",
        {"success": True, "subdomain": result.subdomain},
    )


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request) -> HTMLResponse:
    """Owner/staff dashboard shell (STITCH-07, DASH-01)."""
    return templates.TemplateResponse(request, "dashboard/index.html")


@router.get("/dashboard/loyalty-analytics", response_class=HTMLResponse)
async def loyalty_analytics_page(request: Request) -> HTMLResponse:
    """Loyalty analytics + fraud log shell (STITCH-09, DASH-02)."""
    return templates.TemplateResponse(request, "dashboard/loyalty_analytics.html")
