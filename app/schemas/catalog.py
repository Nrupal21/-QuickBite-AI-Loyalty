"""Schemas for /catalog/* — public reference data with no tenant dimension.

Business categories are the only member today. They live here rather than in
schemas/billing.py because a category is not a billing concept: it is asked
before pricing is shown, and it is what *decides* which plans appear. Putting
it under billing would make the "Join Us" flow read as though choosing a
restaurant type were a payment step.

Everything here is TIER 1 plaintext under AGENTS.md's classification — the
same visibility as a category label on a storefront, never PII.
"""

from pydantic import BaseModel


class BusinessCategoryOut(BaseModel):
    """One row of GET /catalog/business-categories.

    `slug` is the stable wire value; `display_name` is the only string shown
    to a human. `icon_key` names a drawn icon in the frontend's own SVG set,
    never an emoji and never a URL, so the picker renders with no asset fetch
    and no dependency on an external icon host.
    """

    id: str
    slug: str
    display_name: str
    tagline: str
    icon_key: str
