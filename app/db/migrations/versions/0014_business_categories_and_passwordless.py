"""business categories + plan mapping, tenants.category_id, passwordless users

Five independent changes that all land on the "Join Us" / unified-auth path,
kept in one revision because the frontend needs them together or not at all.

**Business categories.** "Join Us" asks what kind of food business this is
before it shows any pricing, because the plan list depends on the answer.
`static.business_categories` holds the taxonomy (reference data — identical
for every tenant, TIER 1 plaintext) and `static.plan_categories` maps plans
onto it. A category with no mapping rows means "every active plan", not "no
plans": see BillingService.list_active_plans, which would otherwise render an
empty pricing page for any category an operator forgot to map.
`restaurant.tenants.category_id` is nullable so every tenant registered
before this migration stays valid.

Neither static table gets RLS. They join `static.roles` and
`static.subscription_plans`, which have none for the same reason: there is no
tenant_id to isolate on, and the rows are the same for everyone.

**Phone as a login identifier.** `restaurant.users.phone_hash` gains a unique
index. It was a plain index because phone was a contact/recovery field only,
which is exactly why identity_service refuses phone as a staff login
identifier ("a phone can match several accounts"). OTP sign-up makes phone a
primary identifier, so it has to be as globally unique as email_hash. Postgres
unique indexes ignore NULLs, so the many accounts with no phone are unaffected;
the trade is that two owners can no longer register the same number, which is
the same constraint email has always carried.

**Passwordless users.** Registration now accepts OTP and OAuth as first-class
sign-up methods, not just email+password (see user_otp_service /
user_oauth_service). An account created that way has no password to hash, so
`restaurant.users.hashed_password` must accept NULL. Login already treats a
NULL hash as an automatic mismatch — `verify_password_constant_time` takes
`str | None` and still pays the bcrypt cost — so no existing code path can be
fooled by the relaxation; AuthService.login turns it into an explicit
"this account signs in without a password" error rather than a generic
credential failure.

**Email is no longer mandatory on an account.** `restaurant.users.email_hash`
and `encrypted_email` become nullable. Registering by phone OTP produces an
account that genuinely has no email yet — the account gets one later, as the
second contact method the "Join Us" flow verifies, exactly mirroring how an
email-registered account gets its phone. The unique index on email_hash stays
and still ignores NULLs, so email remains globally unique wherever it exists.

Callers that read the address (MeResponse, the join-confirmation email) now
handle None rather than assuming a value; nothing writes a placeholder,
because a fake address in an encrypted PII column is worse than an absent one.

Relaxing NOT NULL is an instant, no-rewrite ALTER TABLE (same as 0012's).

Revision ID: 0014
Revises: 0013
Create Date: 2026-08-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Same shape migration 0012 gave users/sessions — a NULL tenant_id row is
# visible to every context, because it belongs to no tenant to isolate from.
_NULL_TENANT_POLICY = (
    "tenant_id IS NULL OR "
    "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
)
_LEGACY_TENANT_POLICY = (
    "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
)


def upgrade() -> None:
    op.create_table(
        "business_categories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        # The stable wire value. Frontend and analytics key on this, so a
        # display rename never invalidates stored data.
        sa.Column("slug", sa.String(), nullable=False, unique=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("tagline", sa.String(), nullable=False),
        # Names a drawn icon in the frontend's own SVG set — never an emoji,
        # never a URL, so the picker renders with no asset fetch.
        sa.Column("icon_key", sa.String(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        schema="static",
    )

    op.create_table(
        "plan_categories",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "plan_id",
            sa.Uuid(),
            sa.ForeignKey("static.subscription_plans.id"),
            nullable=False,
        ),
        sa.Column(
            "category_id",
            sa.Uuid(),
            sa.ForeignKey("static.business_categories.id"),
            nullable=False,
        ),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.UniqueConstraint("plan_id", "category_id", name="uq_plan_category"),
        schema="static",
    )
    op.create_index(
        "ix_plan_categories_plan_id", "plan_categories", ["plan_id"], schema="static"
    )
    op.create_index(
        "ix_plan_categories_category_id", "plan_categories", ["category_id"], schema="static"
    )

    op.add_column(
        "tenants",
        sa.Column(
            "category_id",
            sa.Uuid(),
            sa.ForeignKey("static.business_categories.id"),
            nullable=True,
        ),
        schema="restaurant",
    )
    op.create_index("ix_tenants_category_id", "tenants", ["category_id"], schema="restaurant")

    op.alter_column(
        "users",
        "hashed_password",
        existing_type=sa.String(),
        nullable=True,
        schema="restaurant",
    )

    # An account registered by phone OTP has no email until "Join Us" collects
    # one. The unique index on email_hash is untouched and still ignores NULLs.
    op.alter_column(
        "users", "email_hash", existing_type=sa.String(), nullable=True, schema="restaurant"
    )
    op.alter_column(
        "users", "encrypted_email", existing_type=sa.String(), nullable=True, schema="restaurant"
    )

    # Phone becomes a primary login identifier, so it needs email_hash's
    # uniqueness guarantee. Drop the non-unique index first: leaving both
    # would keep a redundant btree on the same column.
    op.drop_index("ix_users_phone_hash", table_name="users", schema="restaurant")
    op.create_index(
        "ix_users_phone_hash", "users", ["phone_hash"], unique=True, schema="restaurant"
    )

    # A standard user (role USER) has no tenant until they register a
    # restaurant, and OAuth sign-up now creates an identity_links row for
    # exactly that kind of account. Same relaxation and same widened policy
    # shape migration 0012 applied to users/sessions.
    op.alter_column(
        "identity_links",
        "tenant_id",
        existing_type=sa.Uuid(),
        nullable=True,
        schema="restaurant",
    )
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_identity_links ON restaurant.identity_links"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_identity_links ON restaurant.identity_links "
        f"USING ({_NULL_TENANT_POLICY})"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS tenant_isolation_identity_links ON restaurant.identity_links"
    )
    op.execute(
        "CREATE POLICY tenant_isolation_identity_links ON restaurant.identity_links "
        f"USING ({_LEGACY_TENANT_POLICY})"
    )
    # Links belonging to a tenant-less user cannot survive the restored NOT
    # NULL, and there is no tenant to move them to. Unlike an unlink (which
    # sets is_active=false to keep the audit trail), these rows have to go:
    # the column they would keep the trail in is the one being restored.
    # Those users lose only the OAuth shortcut — the account itself, and its
    # audit_logs history, are untouched.
    op.execute(
        "DELETE FROM restaurant.identity_links WHERE tenant_id IS NULL"
    )
    op.alter_column(
        "identity_links",
        "tenant_id",
        existing_type=sa.Uuid(),
        nullable=False,
        schema="restaurant",
    )

    op.drop_index("ix_users_phone_hash", table_name="users", schema="restaurant")
    op.create_index(
        "ix_users_phone_hash", "users", ["phone_hash"], unique=False, schema="restaurant"
    )

    # Phone-registered accounts have no email to restore. There is nothing
    # truthful to backfill — an invented address in an encrypted PII column
    # would be worse than the failure — so those rows are removed along with
    # the sessions and identity links that point at them. Everything else,
    # including every account that ever had an email, is untouched.
    op.execute(
        "DELETE FROM restaurant.identity_links WHERE subject_type = 'user' AND local_id IN "
        "(SELECT id FROM restaurant.users WHERE email_hash IS NULL)"
    )
    op.execute(
        "DELETE FROM restaurant.sessions WHERE user_id IN "
        "(SELECT id FROM restaurant.users WHERE email_hash IS NULL)"
    )
    op.execute(
        "UPDATE restaurant.audit_logs SET user_id = NULL WHERE user_id IN "
        "(SELECT id FROM restaurant.users WHERE email_hash IS NULL)"
    )
    op.execute("DELETE FROM restaurant.users WHERE email_hash IS NULL")
    op.alter_column(
        "users", "encrypted_email", existing_type=sa.String(), nullable=False, schema="restaurant"
    )
    op.alter_column(
        "users", "email_hash", existing_type=sa.String(), nullable=False, schema="restaurant"
    )

    # Any passwordless account would violate the restored NOT NULL. Rather than
    # failing the downgrade halfway through with a constraint error, park those
    # rows on a hash no bcrypt input can ever match: `$2b$` marks it as bcrypt
    # so nothing downstream mis-parses it, and the body is not valid base64-ish
    # bcrypt output, so verify() returns False for every candidate password.
    # Those users must reset their password to sign in on the old code path.
    op.execute(
        "UPDATE restaurant.users SET hashed_password = '$2b$12$disabled.no.password.login' "
        "WHERE hashed_password IS NULL"
    )
    op.alter_column(
        "users",
        "hashed_password",
        existing_type=sa.String(),
        nullable=False,
        schema="restaurant",
    )

    op.drop_index("ix_tenants_category_id", table_name="tenants", schema="restaurant")
    op.drop_column("tenants", "category_id", schema="restaurant")

    op.drop_index("ix_plan_categories_category_id", table_name="plan_categories", schema="static")
    op.drop_index("ix_plan_categories_plan_id", table_name="plan_categories", schema="static")
    op.drop_table("plan_categories", schema="static")
    op.drop_table("business_categories", schema="static")
