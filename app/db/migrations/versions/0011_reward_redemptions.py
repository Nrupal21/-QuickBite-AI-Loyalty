"""reward_redemptions table + RLS (LOYALTY-04)

Stores the 6-char code minted when a customer's `current_reward_count`
reaches a `RewardProgram.stamps_required` (`loyalty_service.py`), and the
staff-facing `POST /loyalty/redeem/{code}` verification of it. Lives in
`restaurant` (with its parent `reward_programs`), not `customer`, because
redemption is an owner/staff-facing verification flow — the `customer_id`
column is a cross-schema reference to `customer.customers` for traceability
only, the same way `customer.stamp_logs` already references back the other
direction.

Enable + FORCE + explicit drop-before-create, matching the current-strength
convention migration 0008 established (0002's original `_enable_rls` helper
only ran `ENABLE`, retroactively hardened to `FORCE` by migration 0006) —
this table is created after 0006, so it should ship at full strength from
the start rather than needing a follow-up hardening migration of its own.

Revision ID: 0011
Revises: 0010
Create Date: 2026-08-13
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reward_redemptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=False),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("restaurant.branches.id"), nullable=False),
        sa.Column(
            "reward_program_id",
            sa.Uuid(),
            sa.ForeignKey("restaurant.reward_programs.id"),
            nullable=False,
        ),
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("customer.customers.id"), nullable=False),
        # 6 alphanumeric chars (app/core/security.py::generate_redemption_code) —
        # unique per tenant, not globally: two tenants may mint the same code
        # independently, and a staff member only ever verifies within their
        # own tenant's RLS-scoped session.
        sa.Column("code", sa.String(length=6), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "redeemed_by_user_id", sa.Uuid(), sa.ForeignKey("restaurant.users.id"), nullable=True
        ),
        schema="restaurant",
    )
    op.create_index(
        "ix_reward_redemptions_tenant_id", "reward_redemptions", ["tenant_id"], schema="restaurant"
    )
    op.create_index(
        "ix_reward_redemptions_customer_id",
        "reward_redemptions",
        ["customer_id"],
        schema="restaurant",
    )
    op.create_unique_constraint(
        "uq_reward_redemptions_tenant_code",
        "reward_redemptions",
        ["tenant_id", "code"],
        schema="restaurant",
    )

    op.execute("ALTER TABLE restaurant.reward_redemptions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE restaurant.reward_redemptions FORCE ROW LEVEL SECURITY")
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_reward_redemptions "
        "ON restaurant.reward_redemptions"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_reward_redemptions ON restaurant.reward_redemptions "
        "USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_reward_redemptions "
        "ON restaurant.reward_redemptions"
    )
    op.drop_table("reward_redemptions", schema="restaurant")
