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
from fastapi.responses import HTMLResponse, RedirectResponse
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


def _firebase_web_context() -> dict:
    """The public Firebase web config the social sign-in buttons need.

    Public web config only — never FIREBASE_SERVICE_ACCOUNT_JSON, which is the
    server's own credential for *verifying* tokens and would be a full account
    takeover if it reached a browser.

    Returns `"null"` when the deployment has no web config, which is what lets
    auth-core.js's `oauth()` report the buttons as unavailable and disable
    them, rather than throwing on an undefined global.
    """
    if not settings.firebase_web_configured:
        return {"firebase_web_config_json": "null", "firebase_web_configured": False}
    return {
        "firebase_web_config_json": json.dumps(
            {
                "apiKey": settings.FIREBASE_WEB_API_KEY,
                "authDomain": settings.FIREBASE_WEB_AUTH_DOMAIN,
                "projectId": settings.FIREBASE_PROJECT_ID,
                "appId": settings.FIREBASE_WEB_APP_ID,
            }
        ),
        "firebase_web_configured": True,
    }


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request) -> HTMLResponse:
    """The one sign-in screen, for every kind of account.

    Diners, owners, and staff all land here and all reach the same
    `restaurant.users` identity through one of three credentials — a one-time
    code, a password, or a social account. Which one an account can actually
    use is never revealed before a credential is offered: that would be the
    enumeration oracle the OTP endpoints exist to avoid.
    """
    return templates.TemplateResponse(
        request, "customer/login.html", _firebase_web_context()
    )


@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request) -> HTMLResponse:
    """Sign-up for a standard account (role USER, no tenant).

    Everyone registers here first, whatever they eventually become.
    Registering never creates a restaurant — that is "Join Us" at /onboarding,
    later and deliberately separate (see AuthService.become_restaurant), so
    that signing up costs nothing and commits to nothing.

    Same Firebase context as /login: the social buttons are on both pages, and
    a half-finished social sign-up started on /login finishes here.
    """
    return templates.TemplateResponse(
        request, "customer/register.html", _firebase_web_context()
    )


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


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request) -> HTMLResponse:
    """"Your profile" — one page for both account types. No server-side auth
    check: static/js/auth-profile.js tries the owner/staff bearer session
    (sessionStorage, GET /auth/me) first, then falls back to the diner
    loyalty session (HttpOnly cookie, GET /api/v1/customers/me), then shows
    a signed-out state. Nothing tenant- or customer-specific is ever baked
    into this shell server-side."""
    return templates.TemplateResponse(request, "customer/profile.html")


@router.get("/onboarding", response_class=HTMLResponse)
async def onboarding_page(request: Request) -> HTMLResponse:
    """"Join Us" — business registration for a signed-in standard user.

    No server-side auth check, same rationale as /dashboard below:
    static/js/auth-onboarding.js reads GET /auth/me with the bearer token from
    sessionStorage and renders a "sign in first" state when there is none. The
    actual mutations — the contact OTP pair and POST /auth/register-restaurant
    — are all authorised server-side regardless of what this shell renders.
    """
    return templates.TemplateResponse(request, "customer/onboarding.html")


@router.get("/rewards")
async def rewards_page() -> RedirectResponse:
    """"My Rewards" now lives at /profile — this URL is kept as a redirect
    for old links/bookmarks rather than duplicating the diner profile view
    across two routes."""
    return RedirectResponse(url="/profile", status_code=301)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request) -> HTMLResponse:
    """Owner/staff dashboard shell (STITCH-07, DASH-01)."""
    return templates.TemplateResponse(request, "dashboard/index.html")


@router.get("/dashboard/loyalty-analytics", response_class=HTMLResponse)
async def loyalty_analytics_page(request: Request) -> HTMLResponse:
    """Loyalty analytics + fraud log shell (STITCH-09, DASH-02)."""
    return templates.TemplateResponse(request, "dashboard/loyalty_analytics.html")


@router.get("/dashboard/google-profile", response_class=HTMLResponse)
async def google_profile_page(request: Request) -> HTMLResponse:
    """Connect/disconnect a branch's Google Business Profile (BRANCH-01,
    REVIEW-03's UI). Same no-server-side-auth-check shell as every other
    dashboard page — see this module's docstring."""
    return templates.TemplateResponse(request, "dashboard/google_profile.html")


@router.get("/dashboard/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    """Team management (AUTH-04's invite/list/deactivate UI). Same no-
    server-side-auth-check shell as every other dashboard page."""
    return templates.TemplateResponse(request, "dashboard/settings.html")


@router.get("/dashboard/loyalty-programs", response_class=HTMLResponse)
async def loyalty_programs_page(request: Request) -> HTMLResponse:
    """Create/list/edit reward programs (BRANCH-01's loyalty-programs CRUD).
    Same no-server-side-auth-check shell as every other dashboard page."""
    return templates.TemplateResponse(request, "dashboard/loyalty_programs.html")


@router.get("/dashboard/qr-codes", response_class=HTMLResponse)
async def qr_codes_page(request: Request) -> HTMLResponse:
    """View/download/regenerate each branch's receipt QR (BRANCH-01). Same
    no-server-side-auth-check shell as every other dashboard page."""
    return templates.TemplateResponse(request, "dashboard/qr_codes.html")


@router.get("/dashboard/reviews", response_class=HTMLResponse)
async def reviews_page(request: Request) -> HTMLResponse:
    """Every synced review plus its AI-drafted reply's approve/reject
    workflow (REVIEW-02). Same no-server-side-auth-check shell as every
    other dashboard page."""
    return templates.TemplateResponse(request, "dashboard/reviews.html")


@router.get("/admin", response_class=HTMLResponse)
async def admin_tenants_page(request: Request) -> HTMLResponse:
    """Super Admin tenants shell (ADMIN-01). Same no-server-side-auth-check
    convention as every /dashboard/* page: admin-shell.js reads GET
    /auth/me-equivalent (the sessionStorage role) and redirects to
    /dashboard if the caller isn't SUPER_ADMIN. Every real mutation is
    still enforced by require_role(SUPER_ADMIN) server-side regardless."""
    return templates.TemplateResponse(request, "admin/index.html")


@router.get("/admin/audit-logs", response_class=HTMLResponse)
async def admin_audit_logs_page(request: Request) -> HTMLResponse:
    """Same shell convention as admin_tenants_page above."""
    return templates.TemplateResponse(request, "admin/audit_logs.html")


@router.get("/admin/monitors", response_class=HTMLResponse)
async def admin_monitors_page(request: Request) -> HTMLResponse:
    """Same shell convention as admin_tenants_page above."""
    return templates.TemplateResponse(request, "admin/monitors.html")
