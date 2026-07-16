"""4-domain schemas + full Doc 2 v3.0 table set + v3.1 hash/encrypt fields

DB-01: create restaurant/customer/payment/static schemas, move existing tables.
DB-02: payment_methods, billing_events, invoices, billing_audit_log.
DB-03: feature_flags, notification_templates.
v3.1:  tenants GSTIN/PAN/owner-phone hash+encrypt, users phone hash+encrypt,
       branches address hash+encrypt (plaintext address/lat/lng dropped),
       sessions ip_address_hash, google_business_profiles token_rotated_at.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-03
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enable_rls(table: str, schema: str, allow_null_tenant: bool = False) -> None:
    policy = "tenant_id = current_setting('app.tenant_id')::uuid"
    if allow_null_tenant:
        policy = f"tenant_id IS NULL OR {policy}"
    op.execute(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation_{table} ON {schema}.{table} USING ({policy})")


def upgrade() -> None:
    # --- DB-01: domain schemas + move existing tables ---
    op.execute("CREATE SCHEMA IF NOT EXISTS restaurant")
    op.execute("CREATE SCHEMA IF NOT EXISTS customer")
    op.execute("CREATE SCHEMA IF NOT EXISTS payment")
    op.execute("CREATE SCHEMA IF NOT EXISTS static")

    op.execute("ALTER TABLE tenants SET SCHEMA restaurant")
    op.execute("ALTER TABLE branches SET SCHEMA restaurant")
    op.execute("ALTER TABLE customers SET SCHEMA customer")
    op.execute("ALTER TABLE stamp_logs SET SCHEMA customer")

    # --- Static domain: roles, subscription_plans, feature_flags, notification_templates ---
    op.create_table(
        "roles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("permissions", JSONB(), nullable=False, server_default="{}"),
        sa.Column("mfa_required", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="static",
    )

    op.create_table(
        "subscription_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("price_monthly_inr", sa.Integer(), nullable=False),
        sa.Column("stripe_price_id", sa.String(), nullable=True),
        sa.Column("feature_limits", JSONB(), nullable=False, server_default="{}"),
        sa.Column("trial_days", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema="static",
    )

    op.create_table(
        "feature_flags",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("key", sa.String(), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("value", JSONB(), nullable=True),
        sa.Column("description", sa.String(), nullable=True),
        schema="static",
    )

    op.create_table(
        "notification_templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("name", sa.String(), nullable=False, unique=True),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("subject", sa.String(), nullable=True),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("is_approved", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="static",
    )

    # --- v3.1: tenants — plan/onboarding + GSTIN/PAN/owner-phone (TIER 3) ---
    op.add_column(
        "tenants",
        sa.Column("plan_id", sa.Uuid(), sa.ForeignKey("static.subscription_plans.id"), nullable=True),
        schema="restaurant",
    )
    op.add_column(
        "tenants",
        sa.Column("onboarding_state", sa.String(), nullable=False, server_default="registered"),
        schema="restaurant",
    )
    op.add_column(
        "tenants",
        sa.Column("timezone", sa.String(), nullable=False, server_default="Asia/Kolkata"),
        schema="restaurant",
    )
    for col in ("gstin_hash", "encrypted_gstin", "pan_hash", "encrypted_pan",
                "owner_phone_hash", "encrypted_owner_phone"):
        op.add_column("tenants", sa.Column(col, sa.String(), nullable=True), schema="restaurant")
    for col in ("gstin_hash", "pan_hash", "owner_phone_hash"):
        op.create_index(f"ix_tenants_{col}", "tenants", [col], schema="restaurant")

    # --- v3.1: branches — address TIER 3, GPS only in PostGIS location ---
    op.add_column(
        "branches",
        sa.Column("address_hash", sa.String(), nullable=False, server_default=""),
        schema="restaurant",
    )
    op.add_column(
        "branches",
        sa.Column("encrypted_address", sa.String(), nullable=False, server_default=""),
        schema="restaurant",
    )
    op.create_index("ix_branches_address_hash", "branches", ["address_hash"], schema="restaurant")
    op.drop_column("branches", "address", schema="restaurant")
    op.drop_column("branches", "latitude", schema="restaurant")
    op.drop_column("branches", "longitude", schema="restaurant")

    # --- Restaurant domain: users, sessions, GMB, reviews, responses, rewards, audit ---
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=False),
        sa.Column("role_id", sa.Uuid(), sa.ForeignKey("static.roles.id"), nullable=False),
        sa.Column("email_hash", sa.String(), nullable=False, unique=True),
        sa.Column("encrypted_email", sa.String(), nullable=False),
        sa.Column("phone_hash", sa.String(), nullable=True),
        sa.Column("encrypted_phone", sa.String(), nullable=True),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("mfa_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("totp_secret", sa.String(), nullable=True),
        sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("failed_login_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        schema="restaurant",
    )
    op.create_index("ix_users_tenant_id", "users", ["tenant_id"], schema="restaurant")
    op.create_index("ix_users_phone_hash", "users", ["phone_hash"], schema="restaurant")
    _enable_rls("users", "restaurant")

    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("restaurant.users.id"), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=False),
        sa.Column("refresh_token_hash", sa.String(), nullable=False, unique=True),
        sa.Column("ip_address_hash", sa.String(), nullable=False),
        sa.Column("user_agent", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="restaurant",
    )
    op.create_index("ix_sessions_user_id", "sessions", ["user_id"], schema="restaurant")
    op.create_index("ix_sessions_tenant_id", "sessions", ["tenant_id"], schema="restaurant")
    _enable_rls("sessions", "restaurant")

    op.create_table(
        "google_business_profiles",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=False),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("restaurant.branches.id"), nullable=False),
        sa.Column("gmb_account_id", sa.String(), nullable=False),
        sa.Column("gmb_location_id", sa.String(), nullable=False),
        sa.Column("encrypted_access_token", sa.String(), nullable=False),
        sa.Column("encrypted_refresh_token", sa.String(), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("token_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("gmb_sync_cursor", sa.String(), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_connected", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema="restaurant",
    )
    op.create_index(
        "ix_google_business_profiles_tenant_id",
        "google_business_profiles", ["tenant_id"], schema="restaurant",
    )
    op.create_index(
        "ix_google_business_profiles_branch_id",
        "google_business_profiles", ["branch_id"], schema="restaurant",
    )
    _enable_rls("google_business_profiles", "restaurant")

    op.create_table(
        "customer_reviews",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=False),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("restaurant.branches.id"), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("external_review_id", sa.String(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("reviewer_name", sa.String(), nullable=True),
        sa.Column("review_body", sa.String(), nullable=True),
        sa.Column("selected_tags", JSONB(), nullable=True),
        sa.Column("sentiment_score", sa.Float(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("source", "external_review_id", name="uq_reviews_source_external"),
        schema="restaurant",
    )
    op.create_index(
        "ix_customer_reviews_tenant_id", "customer_reviews", ["tenant_id"], schema="restaurant"
    )
    op.create_index(
        "ix_customer_reviews_branch_id", "customer_reviews", ["branch_id"], schema="restaurant"
    )
    _enable_rls("customer_reviews", "restaurant")

    op.create_table(
        "review_responses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "review_id", sa.Uuid(), sa.ForeignKey("restaurant.customer_reviews.id"), nullable=False
        ),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=False),
        sa.Column("ai_draft", sa.String(), nullable=False),
        sa.Column("final_text", sa.String(), nullable=True),
        sa.Column("approval_state", sa.String(), nullable=False, server_default="pending"),
        sa.Column(
            "approved_by_user_id", sa.Uuid(), sa.ForeignKey("restaurant.users.id"), nullable=True
        ),
        sa.Column("ai_model_used", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False, unique=True),
        schema="restaurant",
    )
    op.create_index(
        "ix_review_responses_review_id", "review_responses", ["review_id"], schema="restaurant"
    )
    op.create_index(
        "ix_review_responses_tenant_id", "review_responses", ["tenant_id"], schema="restaurant"
    )
    _enable_rls("review_responses", "restaurant")

    op.create_table(
        "reward_programs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=False),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("restaurant.branches.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("stamps_required", sa.Integer(), nullable=False),
        sa.Column("reward_type", sa.String(), nullable=False),
        sa.Column("reward_value", sa.String(), nullable=False),
        sa.Column("validity_days", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema="restaurant",
    )
    op.create_index(
        "ix_reward_programs_tenant_id", "reward_programs", ["tenant_id"], schema="restaurant"
    )
    op.create_index(
        "ix_reward_programs_branch_id", "reward_programs", ["branch_id"], schema="restaurant"
    )
    _enable_rls("reward_programs", "restaurant")

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=True),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("restaurant.users.id"), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("resource_type", sa.String(), nullable=True),
        sa.Column("resource_id", sa.Uuid(), nullable=True),
        sa.Column("ip_address_hash", sa.String(), nullable=True),
        sa.Column("metadata", JSONB(), nullable=True),
        schema="restaurant",
    )
    op.create_index("ix_audit_logs_tenant_id", "audit_logs", ["tenant_id"], schema="restaurant")
    op.create_index("ix_audit_logs_user_id", "audit_logs", ["user_id"], schema="restaurant")
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"], schema="restaurant")
    _enable_rls("audit_logs", "restaurant", allow_null_tenant=True)

    # --- Customer domain: per-tenant uniqueness on hashed lookups ---
    op.create_unique_constraint(
        "uq_customers_tenant_phone", "customers", ["tenant_id", "phone_hash"], schema="customer"
    )
    op.create_unique_constraint(
        "uq_customers_tenant_email", "customers", ["tenant_id", "email_hash"], schema="customer"
    )

    # --- Payment domain (DB-02) ---
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"),
            nullable=False, unique=True,
        ),
        sa.Column(
            "plan_id", sa.Uuid(), sa.ForeignKey("static.subscription_plans.id"), nullable=False
        ),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("stripe_customer_id", sa.String(), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(), nullable=True),
        sa.Column("current_period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="payment",
    )
    _enable_rls("subscriptions", "payment")

    op.create_table(
        "usage_tracking",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=False),
        sa.Column("period_year", sa.Integer(), nullable=False),
        sa.Column("period_month", sa.Integer(), nullable=False),
        sa.Column("ai_responses_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sms_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("whatsapp_sent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("loyalty_scans_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("warning_sent", JSONB(), nullable=False, server_default="{}"),
        sa.UniqueConstraint(
            "tenant_id", "period_year", "period_month", name="uq_usage_tenant_period"
        ),
        schema="payment",
    )
    op.create_index(
        "ix_usage_tracking_tenant_id", "usage_tracking", ["tenant_id"], schema="payment"
    )
    _enable_rls("usage_tracking", "payment")

    op.create_table(
        "payment_methods",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=False),
        sa.Column("stripe_payment_method_id", sa.String(), nullable=False, unique=True),
        sa.Column("card_brand", sa.String(), nullable=True),
        sa.Column("card_last4", sa.String(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        schema="payment",
    )
    op.create_index(
        "ix_payment_methods_tenant_id", "payment_methods", ["tenant_id"], schema="payment"
    )
    _enable_rls("payment_methods", "payment")

    op.create_table(
        "billing_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=True),
        sa.Column("stripe_event_id", sa.String(), nullable=False, unique=True),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("processed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="payment",
    )
    op.create_index(
        "ix_billing_events_tenant_id", "billing_events", ["tenant_id"], schema="payment"
    )
    op.create_index(
        "ix_billing_events_event_type", "billing_events", ["event_type"], schema="payment"
    )
    _enable_rls("billing_events", "payment", allow_null_tenant=True)

    op.create_table(
        "invoices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=False),
        sa.Column("stripe_invoice_id", sa.String(), nullable=False, unique=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("amount_total_inr", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(), nullable=False, server_default="inr"),
        sa.Column("invoice_pdf_url", sa.String(), nullable=True),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        schema="payment",
    )
    op.create_index("ix_invoices_tenant_id", "invoices", ["tenant_id"], schema="payment")
    _enable_rls("invoices", "payment")

    op.create_table(
        "billing_audit_log",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=True),
        sa.Column("service_name", sa.String(), nullable=False),
        sa.Column("db_role", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("table_name", sa.String(), nullable=False),
        sa.Column("row_id", sa.Uuid(), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="payment",
    )
    op.create_index(
        "ix_billing_audit_log_tenant_id", "billing_audit_log", ["tenant_id"], schema="payment"
    )
    _enable_rls("billing_audit_log", "payment", allow_null_tenant=True)

    # --- Payment schema lockdown (SEC-31): only app_payment_rw, no DELETE ---
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'app_payment_rw') THEN
                CREATE ROLE app_payment_rw NOLOGIN;
            END IF;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON SCHEMA payment FROM PUBLIC")
    op.execute("GRANT USAGE ON SCHEMA payment TO app_payment_rw")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA payment TO app_payment_rw"
    )


def downgrade() -> None:
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA payment FROM app_payment_rw")
    op.execute("REVOKE ALL ON SCHEMA payment FROM app_payment_rw")

    for table in (
        "billing_audit_log", "invoices", "billing_events",
        "payment_methods", "usage_tracking", "subscriptions",
    ):
        op.drop_table(table, schema="payment")

    op.drop_constraint("uq_customers_tenant_email", "customers", schema="customer")
    op.drop_constraint("uq_customers_tenant_phone", "customers", schema="customer")

    for table in (
        "audit_logs", "reward_programs", "review_responses",
        "customer_reviews", "google_business_profiles", "sessions", "users",
    ):
        op.drop_table(table, schema="restaurant")

    op.add_column(
        "branches",
        sa.Column("address", sa.String(), nullable=False, server_default=""),
        schema="restaurant",
    )
    op.add_column(
        "branches",
        sa.Column("latitude", sa.Float(), nullable=False, server_default="0"),
        schema="restaurant",
    )
    op.add_column(
        "branches",
        sa.Column("longitude", sa.Float(), nullable=False, server_default="0"),
        schema="restaurant",
    )
    op.drop_index("ix_branches_address_hash", "branches", schema="restaurant")
    op.drop_column("branches", "encrypted_address", schema="restaurant")
    op.drop_column("branches", "address_hash", schema="restaurant")

    for col in ("gstin_hash", "pan_hash", "owner_phone_hash"):
        op.drop_index(f"ix_tenants_{col}", "tenants", schema="restaurant")
    for col in (
        "encrypted_owner_phone", "owner_phone_hash", "encrypted_pan", "pan_hash",
        "encrypted_gstin", "gstin_hash", "timezone", "onboarding_state", "plan_id",
    ):
        op.drop_column("tenants", col, schema="restaurant")

    for table in ("notification_templates", "feature_flags", "subscription_plans", "roles"):
        op.drop_table(table, schema="static")

    op.execute("ALTER TABLE customer.stamp_logs SET SCHEMA public")
    op.execute("ALTER TABLE customer.customers SET SCHEMA public")
    op.execute("ALTER TABLE restaurant.branches SET SCHEMA public")
    op.execute("ALTER TABLE restaurant.tenants SET SCHEMA public")

    op.execute("DROP SCHEMA IF EXISTS static")
    op.execute("DROP SCHEMA IF EXISTS payment")
    op.execute("DROP SCHEMA IF EXISTS customer")
    op.execute("DROP SCHEMA IF EXISTS restaurant")
