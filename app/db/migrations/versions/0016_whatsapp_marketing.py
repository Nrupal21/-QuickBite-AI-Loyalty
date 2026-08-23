"""WhatsApp marketing: whatsapp_business_accounts, whatsapp_templates,
marketing_campaigns, campaign_recipients + customers.whatsapp_marketing_opt_in

Per-tenant, bring-your-own Meta WhatsApp Business Account (WABA). All four new
tables live in `restaurant` (see app/db/models/whatsapp.py's docstring for why
campaign_recipients sits there despite referencing customer.customers, same as
migration 0011's reward_redemptions) and ship RLS at full strength from the
start, matching the convention 0008 established and 0011 already follows.

whatsapp_marketing_opt_in is a new customers column, separate from the
existing whatsapp_opt_in (transactional alerts) — Meta requires distinct
consent for MARKETING-category template sends. Defaults False so no existing
customer is silently opted into broadcast messages by this migration.

Revision ID: 0016
Revises: 0015
Create Date: 2026-08-22
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE restaurant.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE restaurant.{table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON restaurant.{table}")
    op.execute(
        f"CREATE POLICY tenant_isolation_{table} ON restaurant.{table} "
        "USING (tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid)"
    )


def _drop_rls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation_{table} ON restaurant.{table}")


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column(
            "whatsapp_marketing_opt_in", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        schema="customer",
    )

    op.create_table(
        "whatsapp_business_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=False),
        sa.Column("waba_id", sa.String(), nullable=False),
        sa.Column("phone_number_id", sa.String(), nullable=False),
        sa.Column("business_id", sa.String(), nullable=False),
        sa.Column("phone_hash", sa.String(), nullable=False),
        sa.Column("encrypted_display_phone_number", sa.String(), nullable=False),
        sa.Column("encrypted_access_token", sa.String(), nullable=False),
        sa.Column("token_exchanged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("quality_rating", sa.String(), nullable=True),
        sa.Column("messaging_tier", sa.String(), nullable=True),
        sa.Column("webhook_subscribed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_connected", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        schema="restaurant",
    )
    op.create_index(
        "ix_whatsapp_business_accounts_tenant_id",
        "whatsapp_business_accounts",
        ["tenant_id"],
        schema="restaurant",
    )
    op.create_index(
        "ix_whatsapp_business_accounts_phone_hash",
        "whatsapp_business_accounts",
        ["phone_hash"],
        schema="restaurant",
    )
    op.create_unique_constraint(
        "uq_whatsapp_business_accounts_tenant",
        "whatsapp_business_accounts",
        ["tenant_id"],
        schema="restaurant",
    )

    op.create_table(
        "whatsapp_templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=False),
        sa.Column(
            "waba_account_id",
            sa.Uuid(),
            sa.ForeignKey("restaurant.whatsapp_business_accounts.id"),
            nullable=False,
        ),
        sa.Column("meta_template_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("language", sa.String(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("components", JSONB(), nullable=False, server_default="[]"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=False),
        schema="restaurant",
    )
    op.create_index(
        "ix_whatsapp_templates_tenant_id", "whatsapp_templates", ["tenant_id"], schema="restaurant"
    )
    op.create_index(
        "ix_whatsapp_templates_waba_account_id",
        "whatsapp_templates",
        ["waba_account_id"],
        schema="restaurant",
    )
    op.create_unique_constraint(
        "uq_whatsapp_templates_tenant_name_lang",
        "whatsapp_templates",
        ["tenant_id", "name", "language"],
        schema="restaurant",
    )

    op.create_table(
        "marketing_campaigns",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=False),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("restaurant.branches.id"), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "template_id", sa.Uuid(), sa.ForeignKey("restaurant.whatsapp_templates.id"), nullable=False
        ),
        sa.Column("audience_filter", JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(), nullable=False, server_default="draft"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_user_id", sa.Uuid(), sa.ForeignKey("restaurant.users.id"), nullable=False
        ),
        sa.Column("recipients_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sent_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivered_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("read_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_count", sa.Integer(), nullable=False, server_default="0"),
        schema="restaurant",
    )
    op.create_index(
        "ix_marketing_campaigns_tenant_id", "marketing_campaigns", ["tenant_id"], schema="restaurant"
    )

    op.create_table(
        "campaign_recipients",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=False),
        sa.Column(
            "campaign_id", sa.Uuid(), sa.ForeignKey("restaurant.marketing_campaigns.id"), nullable=False
        ),
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("customer.customers.id"), nullable=False),
        sa.Column("wa_message_id", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="queued"),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.Column("error_message", sa.String(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        schema="restaurant",
    )
    op.create_index(
        "ix_campaign_recipients_tenant_id", "campaign_recipients", ["tenant_id"], schema="restaurant"
    )
    op.create_index(
        "ix_campaign_recipients_campaign_id", "campaign_recipients", ["campaign_id"], schema="restaurant"
    )
    op.create_index(
        "ix_campaign_recipients_customer_id", "campaign_recipients", ["customer_id"], schema="restaurant"
    )
    op.create_index(
        "ix_campaign_recipients_wa_message_id",
        "campaign_recipients",
        ["wa_message_id"],
        schema="restaurant",
    )
    op.create_unique_constraint(
        "uq_campaign_recipients_campaign_customer",
        "campaign_recipients",
        ["campaign_id", "customer_id"],
        schema="restaurant",
    )

    for table in (
        "whatsapp_business_accounts",
        "whatsapp_templates",
        "marketing_campaigns",
        "campaign_recipients",
    ):
        _rls(table)


def downgrade() -> None:
    for table in (
        "campaign_recipients",
        "marketing_campaigns",
        "whatsapp_templates",
        "whatsapp_business_accounts",
    ):
        _drop_rls(table)
        op.drop_table(table, schema="restaurant")

    op.drop_column("customers", "whatsapp_marketing_opt_in", schema="customer")
