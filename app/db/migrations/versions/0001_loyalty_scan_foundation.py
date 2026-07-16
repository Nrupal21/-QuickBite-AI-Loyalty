"""loyalty scan foundation — tenants, customers, branches, stamp_logs

Revision ID: 0001
Revises:
Create Date: 2026-06-30
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from geoalchemy2 import Geometry

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")

    op.create_table(
        "tenants",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("subdomain", sa.String(), nullable=False, unique=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )

    op.create_table(
        "customers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("phone_hash", sa.String(), nullable=False),
        sa.Column("encrypted_phone", sa.String(), nullable=False),
        sa.Column("email_hash", sa.String(), nullable=True),
        sa.Column("encrypted_email", sa.String(), nullable=True),
        sa.Column("encrypted_name", sa.String(), nullable=True),
        sa.Column("whatsapp_opt_in", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("total_stamps_alltime", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_reward_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("otp_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_customers_tenant_id", "customers", ["tenant_id"])
    op.create_index("ix_customers_phone_hash", "customers", ["phone_hash"])
    op.execute("ALTER TABLE customers ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_customers ON customers "
        "USING (tenant_id = current_setting('app.tenant_id')::uuid)"
    )

    op.create_table(
        "branches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("address", sa.String(), nullable=False),
        sa.Column("latitude", sa.Float(), nullable=False),
        sa.Column("longitude", sa.Float(), nullable=False),
        sa.Column("location", Geometry(geometry_type="POINT", srid=4326), nullable=False),
        sa.Column("geofence_radius_m", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("qr_code_token", sa.String(), nullable=False, unique=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_branches_tenant_id", "branches", ["tenant_id"])
    op.create_index("ix_branches_qr_code_token", "branches", ["qr_code_token"])
    op.execute("CREATE INDEX ix_branches_location_gist ON branches USING GIST (location)")
    op.execute("ALTER TABLE branches ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_branches ON branches "
        "USING (tenant_id = current_setting('app.tenant_id')::uuid)"
    )

    op.create_table(
        "stamp_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("tenant_id", sa.Uuid(), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("branch_id", sa.Uuid(), sa.ForeignKey("branches.id"), nullable=False),
        sa.Column("customer_id", sa.Uuid(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("customer_phone_hash", sa.String(), nullable=True),
        sa.Column("gps_latitude_at_scan", sa.Float(), nullable=False),
        sa.Column("gps_longitude_at_scan", sa.Float(), nullable=False),
        sa.Column("distance_from_branch_m", sa.Float(), nullable=False),
        sa.Column("is_fraudulent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("scanned_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_stamp_logs_tenant_id", "stamp_logs", ["tenant_id"])
    op.create_index("ix_stamp_logs_branch_id", "stamp_logs", ["branch_id"])
    op.create_index("ix_stamp_logs_customer_id", "stamp_logs", ["customer_id"])
    op.create_index("ix_stamp_logs_customer_phone_hash", "stamp_logs", ["customer_phone_hash"])
    op.execute("ALTER TABLE stamp_logs ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation_stamp_logs ON stamp_logs "
        "USING (tenant_id = current_setting('app.tenant_id')::uuid)"
    )


def downgrade() -> None:
    op.drop_table("stamp_logs")
    op.drop_table("branches")
    op.drop_table("customers")
    op.drop_table("tenants")
