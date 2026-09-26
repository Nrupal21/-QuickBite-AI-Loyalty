"""stamp_logs.anon_identity_hash — link anonymous scans to a new customer

NEW-OTP-03: an anonymous scan stores the SHA-256 of the scanner's IP (Tier 2,
hash only) so /customers/register can attach that stamp to the new customer_id.
Nullable — existing rows and authenticated scans leave it NULL. RLS on
customer.stamp_logs already covers the new column.

Revision ID: 0021
Revises: 0020
Create Date: 2026-09-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stamp_logs",
        sa.Column("anon_identity_hash", sa.String(), nullable=True),
        schema="customer",
    )
    op.create_index(
        "ix_stamp_logs_anon_identity_hash", "stamp_logs", ["anon_identity_hash"], schema="customer"
    )


def downgrade() -> None:
    op.drop_index("ix_stamp_logs_anon_identity_hash", table_name="stamp_logs", schema="customer")
    op.drop_column("stamp_logs", "anon_identity_hash", schema="customer")
