"""QuickBite — Public reference data: /catalog/*.

Unauthenticated by design. The "Join Us" flow asks a signed-in standard user
what kind of business they run *before* it shows any pricing, and the landing
site's pricing page wants the same list with no session at all. Nothing here
is tenant-scoped, so there is no RLS context to bind and nothing an anonymous
caller learns that a storefront sign would not tell them.

Rate-limited anyway: the list is small and cacheable, and an unauthenticated
GET with no limit is a free amplification target.
"""

import structlog
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.rate_limiter import limiter
from app.db.base import get_db
from app.db.models.static_data import BusinessCategory
from app.schemas.catalog import BusinessCategoryOut

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/catalog", tags=["catalog"])


@router.get("/business-categories", response_model=list[BusinessCategoryOut])
@limiter.limit("30/minute")
async def list_business_categories(
    request: Request,
    session: AsyncSession = Depends(get_db),
) -> list[BusinessCategoryOut]:
    """The category picker's source list, ordered the way it should render.

    `sort_order` is an explicit column rather than an alphabetical sort: the
    picker leads with the categories most signups actually pick, and "Bakery
    & Sweets" landing above "Restaurant" purely because of its first letter
    would bury the common case.
    """
    result = await session.execute(
        select(BusinessCategory)
        .where(BusinessCategory.is_active.is_(True))
        .order_by(BusinessCategory.sort_order, BusinessCategory.display_name)
    )
    return [
        BusinessCategoryOut(
            id=str(category.id),
            slug=category.slug,
            display_name=category.display_name,
            tagline=category.tagline,
            icon_key=category.icon_key,
        )
        for category in result.scalars().all()
    ]
