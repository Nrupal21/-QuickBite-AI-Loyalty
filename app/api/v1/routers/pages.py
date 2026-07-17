"""QuickBite — Server-rendered pages (Jinja2), mounted WITHOUT the /api/v1 prefix.

Landing page (STITCH-12) lives here. Customer flow pages (scan, loyalty card,
OTP screens) join as their STITCH tickets land.
"""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")

router = APIRouter(tags=["pages"])


@router.get("/", response_class=HTMLResponse)
async def landing_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "landing/index.html")
