"""branch_prize_pool + scratch_cards tables + RLS (NICE-01)

`restaurant.branch_prize_pool` is the owner-configured list of possible
scratch-card prizes per branch — same shape/reasoning as `reward_programs`
(migration in the original schema-domain split). `customer.scratch_cards` is
the diner-facing scratch-off record, one per 5th-stamp opportunity granted
on a Pro+ plan — same reasoning as `customer.stamp_logs`/`customer.
review_drafts`: it's the diner's own activity history, tenant-scoped RLS
like every other table in this schema.

Ships FORCE + drop-before-create from the start, matching migrations
0011/0013's precedent for tables created after 0006.

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-06
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "branch_prize_pool",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=False),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("restaurant.branches.id"), nullable=False),
        sa.Column("prize_label", sa.String(), nullable=False),
        sa.Column("weight", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema="restaurant",
    )
    op.create_index(
        "ix_branch_prize_pool_tenant_id", "branch_prize_pool", ["tenant_id"], schema="restaurant"
    )
    op.create_index(
        "ix_branch_prize_pool_branch_id", "branch_prize_pool", ["branch_id"], schema="restaurant"
    )
    op.execute("ALTER TABLE restaurant.branch_prize_pool ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE restaurant.branch_prize_pool FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_branch_prize_pool "
        "ON restaurant.branch_prize_pool"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_branch_prize_pool ON restaurant.branch_prize_pool "
        "USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"
    )

    op.create_table(
        "scratch_cards",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=False),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("restaurant.branches.id"), nullable=False),
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("customer.customers.id"), nullable=False),
        sa.Column("prize_label", sa.String(), nullable=False),
        # 6 alphanumeric chars, same generator as reward_redemptions.code —
        # unique-per-tenant is not enforced here (unlike migration 0011) since
        # a scratch card is never looked up by code alone; it's always
        # accessed by its own `id` via the owning customer's session.
        sa.Column("redemption_code", sa.String(length=6), nullable=False),
        sa.Column("is_revealed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("revealed_at", sa.DateTime(timezone=True), nullable=True),
        schema="customer",
    )
    op.create_index(
        "ix_scratch_cards_tenant_id", "scratch_cards", ["tenant_id"], schema="customer"
    )
    op.create_index(
        "ix_scratch_cards_branch_id", "scratch_cards", ["branch_id"], schema="customer"
    )
    op.create_index(
        "ix_scratch_cards_customer_id", "scratch_cards", ["customer_id"], schema="customer"
    )
    op.execute("ALTER TABLE customer.scratch_cards ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE customer.scratch_cards FORCE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_scratch_cards ON customer.scratch_cards")
    op.execute(
        "CREATE POLICY tenant_isolation_scratch_cards ON customer.scratch_cards "
        "USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_scratch_cards ON customer.scratch_cards")
    op.drop_table("scratch_cards", schema="customer")
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_branch_prize_pool "
        "ON restaurant.branch_prize_pool"
    )
    op.drop_table("branch_prize_pool", schema="restaurant")
