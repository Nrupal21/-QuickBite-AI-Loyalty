"""Add username as a third login identifier (Owner/Staff + Customer)

Adds TIER 3 (hash + encrypt) username fields, mirroring the existing
email_hash/encrypted_email pattern. users.username_hash is globally unique
(mirrors email_hash); customers.username_hash is tenant-scoped unique
(mirrors phone_hash/email_hash). Both columns are nullable — optional
identifier, no breaking change to existing registrations.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("username_hash", sa.String(), nullable=True, unique=True),
        schema="restaurant",
    )
    op.add_column(
        "users", sa.Column("encrypted_username", sa.String(), nullable=True), schema="restaurant"
    )
    op.create_index("ix_users_username_hash", "users", ["username_hash"], schema="restaurant")

    op.add_column(
        "customers", sa.Column("username_hash", sa.String(), nullable=True), schema="customer"
    )
    op.add_column(
        "customers", sa.Column("encrypted_username", sa.String(), nullable=True), schema="customer"
    )
    op.create_index("ix_customers_username_hash", "customers", ["username_hash"], schema="customer")
    op.create_unique_constraint(
        "uq_customers_tenant_username",
        "customers",
        ["tenant_id", "username_hash"],
        schema="customer",
    )


def downgrade() -> None:
    op.drop_constraint("uq_customers_tenant_username", "customers", schema="customer")
    op.drop_index("ix_customers_username_hash", "customers", schema="customer")
    op.drop_column("customers", "encrypted_username", schema="customer")
    op.drop_column("customers", "username_hash", schema="customer")

    op.drop_index("ix_users_username_hash", "users", schema="restaurant")
    op.drop_column("users", "encrypted_username", schema="restaurant")
    op.drop_column("users", "username_hash", schema="restaurant")
