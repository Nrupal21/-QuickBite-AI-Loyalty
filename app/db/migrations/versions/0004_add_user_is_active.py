"""Add users.is_active for soft-deleting team members (AUTH-04)

Removing a team member must not DELETE the row: audit_logs.user_id is a FK
onto restaurant.users, and a security trail whose actor has vanished is
worthless. Owners deactivate instead, and get_current_user()/login() refuse
inactive accounts.

server_default='true' backfills every existing row as active in the same
statement — without it the NOT NULL column cannot be added to a populated
table.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema="restaurant",
    )


def downgrade() -> None:
    op.drop_column("users", "is_active", schema="restaurant")
