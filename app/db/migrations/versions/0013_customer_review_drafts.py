"""customer.review_drafts table + RLS (customer profile: "reviews you've sent")

Records each AI-drafted review a *logged-in* customer generated via the QR
composer (POST /reviews/generate) — the source for the customer profile
page's "Reviews you've sent" list. `review_service.generate_review_draft`
already runs anonymously by design (REVIEW-01: "No auth required" — most
diners never log in before scanning); this table only fills when an
already-authenticated customer happens to be signed in at draft time
(`get_current_customer_optional`), so it's a proxy for "reviews you
drafted while signed in", not a global review-attribution system.
`restaurant.customer_reviews` (the business's actual received reviews,
synced from Google/Yelp) carries no customer_id at all and is not touched by
this migration — a public Google review cannot be reliably matched back to
an internal Customer row, so that stays a separate, unrelated table.

Lives in `customer` schema, matching `customer.stamp_logs`'s reasoning:
customer-schema tables carry the diner's own activity history, tenant-scoped
RLS same as every other table in this schema. Ships FORCE + drop-before-create
from the start, matching migration 0011's precedent for post-0006 tables.

Revision ID: 0013
Revises: 0012
Create Date: 2026-08-18
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_drafts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=False),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("restaurant.branches.id"), nullable=False),
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("customer.customers.id"), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("tags", JSONB(), nullable=False, server_default="[]"),
        # Truncated preview only (see review_service.py) — the full draft is
        # never persisted, since it is customer-authored free text the diner
        # may heavily edit or never actually post.
        sa.Column("draft_excerpt", sa.String(length=280), nullable=False),
        schema="customer",
    )
    op.create_index(
        "ix_review_drafts_tenant_id", "review_drafts", ["tenant_id"], schema="customer"
    )
    op.create_index(
        "ix_review_drafts_customer_id", "review_drafts", ["customer_id"], schema="customer"
    )

    op.execute("ALTER TABLE customer.review_drafts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE customer.review_drafts FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_review_drafts ON customer.review_drafts")
    op.execute(
        "CREATE POLICY tenant_isolation_review_drafts ON customer.review_drafts "
        "USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_review_drafts ON customer.review_drafts")
    op.drop_table("review_drafts", schema="customer")
