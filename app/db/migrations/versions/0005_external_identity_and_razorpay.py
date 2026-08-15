"""External identity links, provider-agnostic billing, Firestore outbox

Three related changes:

1. restaurant.identity_links — the cross-reference that maps a Supabase or
   Firebase subject onto a local users/customers row. Explicit table rather
   than email matching at request time: an external provider's email claim is
   its assertion about identity, and treating it as a join key makes account
   takeover a matter of registering the right address.

2. Provider-agnostic billing columns. The stripe_* columns were never written
   to by any code path (billing_service.py was a stub), so renaming now is
   free; renaming after go-live is not. Razorpay is the actual gateway —
   pricing is already INR paise and tenants carry GSTIN/PAN.

3. payment.projection_outbox — the atomic hand-off to the Firestore payment
   mirror. Rows are written inside the same transaction as the ledger change,
   so a crash can never leave Postgres updated and the mirror unaware.

Plus users.tokens_valid_from / customers.tokens_valid_from: external tokens
are outside the `revoked_jti:` Redis convention (Supabase and Firebase expose
no server-side per-session handle), so logout and deactivation revoke them by
moving this watermark past the token's `iat`.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-27
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enable_rls(table: str, schema: str, allow_null_tenant: bool = False) -> None:
    """Same helper as 0002 — copied rather than imported, since Alembic
    revisions must stay independently runnable."""
    policy = "tenant_id = current_setting('app.tenant_id')::uuid"
    if allow_null_tenant:
        policy = f"tenant_id IS NULL OR {policy}"
    op.execute(f"ALTER TABLE {schema}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"CREATE POLICY tenant_isolation_{table} ON {schema}.{table} USING ({policy})")


# Old -> new, for the provider-agnostic rename. (schema, table, old, new)
_PROVIDER_RENAMES: tuple[tuple[str, str, str, str], ...] = (
    ("static", "subscription_plans", "stripe_price_id", "provider_plan_id"),
    ("payment", "subscriptions", "stripe_customer_id", "provider_customer_ref"),
    ("payment", "subscriptions", "stripe_subscription_id", "provider_subscription_ref"),
    ("payment", "billing_events", "stripe_event_id", "provider_event_id"),
    ("payment", "invoices", "stripe_invoice_id", "provider_invoice_ref"),
    ("payment", "payment_methods", "stripe_payment_method_id", "provider_method_ref"),
)

# Tables that gain a `provider` discriminator.
_PROVIDER_COLUMN_TABLES: tuple[tuple[str, str], ...] = (
    ("payment", "subscriptions"),
    ("payment", "invoices"),
    ("payment", "payment_methods"),
    ("payment", "billing_events"),
)


def upgrade() -> None:
    # --- 1. identity_links -------------------------------------------------
    #
    # DELIBERATELY NOT RLS-PROTECTED. This is the bootstrap table: the request
    # cannot know its tenant until this row is read, so a
    # `tenant_id = current_setting('app.tenant_id')` policy would make every
    # external-token request return zero rows and fail closed with no
    # diagnosable cause. Do not "fix" this by adding a policy.
    #
    # Compensating controls: the row stores only a SHA-256 hash and AES-256-GCM
    # ciphertext of the provider subject (never a cleartext identifier), it is
    # only ever queried by exact hash equality, and tenant_id read from here is
    # immediately used to set app.tenant_id — so the follow-up SELECT of the
    # users/customers row is itself RLS-checked. A wrong tenant_id here yields
    # zero rows and a 401, not a cross-tenant read.
    op.create_table(
        "identity_links",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("provider", sa.String(), nullable=False),  # supabase | firebase
        # sha256("<provider>:<sub>") — the provider is inside the hash so a
        # Firebase subject can never collide with a Supabase one.
        sa.Column("provider_subject_hash", sa.String(), nullable=False),
        sa.Column("encrypted_provider_subject", sa.String(), nullable=False),
        sa.Column("subject_type", sa.String(), nullable=False),  # user | customer
        # No FK: local_id points at restaurant.users OR customer.customers
        # depending on subject_type, and PostgreSQL has no polymorphic FK.
        sa.Column("local_id", sa.Uuid(), nullable=False),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=False
        ),
        sa.Column("linked_via", sa.String(), nullable=False),  # explicit | email | phone
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        schema="restaurant",
    )
    op.create_index(
        "ix_identity_links_tenant_id", "identity_links", ["tenant_id"], schema="restaurant"
    )
    op.create_index(
        "ix_identity_links_local_id", "identity_links", ["local_id"], schema="restaurant"
    )
    # The lookup index. Unique because one external subject maps to exactly one
    # local principal — without this, a duplicate row would make which account
    # you authenticate as depend on row order.
    op.create_unique_constraint(
        "uq_identity_links_provider_subject",
        "identity_links",
        ["provider", "provider_subject_hash"],
        schema="restaurant",
    )
    # And one local principal has at most one identity per provider, so a
    # second Supabase account cannot be silently attached to the same user.
    op.create_unique_constraint(
        "uq_identity_links_provider_local",
        "identity_links",
        ["provider", "subject_type", "local_id"],
        schema="restaurant",
    )

    # --- 2. token revocation watermark -------------------------------------
    # server_default=now() backfills existing rows in the same statement; a
    # NOT NULL column cannot otherwise be added to a populated table.
    for schema, table in (("restaurant", "users"), ("customer", "customers")):
        op.add_column(
            table,
            sa.Column(
                "tokens_valid_from",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            schema=schema,
        )

    # --- 3. provider-agnostic billing columns ------------------------------
    for schema, table, old, new in _PROVIDER_RENAMES:
        op.alter_column(table, old, new_column_name=new, schema=schema)

    for schema, table in _PROVIDER_COLUMN_TABLES:
        op.add_column(
            table,
            sa.Column("provider", sa.String(), nullable=False, server_default="razorpay"),
            schema=schema,
        )

    # Razorpay delivers at-least-once and does NOT guarantee ordering, so a
    # retried `subscription.updated` can arrive after `subscription.cancelled`.
    # Storing the event's own timestamp lets the handler drop anything older
    # than what it has already applied. billing_events gets it for audit
    # (which event this row represents); subscriptions gets it as the
    # staleness watermark billing_service actually compares against.
    op.add_column(
        "billing_events",
        sa.Column("provider_event_at", sa.DateTime(timezone=True), nullable=True),
        schema="payment",
    )
    op.add_column(
        "subscriptions",
        sa.Column("provider_event_at", sa.DateTime(timezone=True), nullable=True),
        schema="payment",
    )

    # --- 4. projection_outbox ----------------------------------------------
    op.create_table(
        "projection_outbox",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column(
            "tenant_id", sa.Uuid(), sa.ForeignKey("restaurant.tenants.id"), nullable=False
        ),
        sa.Column("aggregate_type", sa.String(), nullable=False),  # subscription|invoice|payment
        sa.Column("aggregate_id", sa.Uuid(), nullable=False),
        # Monotonic per aggregate. The worker drains with SKIP LOCKED and so
        # delivers out of order; the Firestore write drops any version <= the
        # one already stored, which is what stops a stale projection from
        # overwriting a fresh one.
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        schema="payment",
    )
    op.create_index(
        "ix_projection_outbox_tenant_id", "projection_outbox", ["tenant_id"], schema="payment"
    )
    # Partial index: the worker only ever scans pending rows, and sent rows
    # accumulate forever. Indexing them all would grow without bound for no
    # query that reads them.
    op.execute(
        "CREATE INDEX ix_projection_outbox_pending "
        "ON payment.projection_outbox (next_attempt_at) WHERE status = 'pending'"
    )
    # DELIBERATELY NOT RLS-PROTECTED, unlike every other payment.* table. The
    # drain worker must scan pending rows ACROSS every tenant in a single
    # `SELECT ... FOR UPDATE SKIP LOCKED` query — RLS pins a session to
    # exactly one tenant_id at a time, so a cross-tenant scan is structurally
    # impossible under a tenant-isolation policy, not just inconvenient.
    # Compensating control: this table is never read by anything except
    # billing_service (which only ever inserts) and projection_service (the
    # worker) — no request handler exposes it, so there is no path for one
    # tenant to read another's queued projection through this table.

    # The outbox worker and the webhook handler both run as app_payment_rw
    # (granted SELECT/INSERT/UPDATE on the payment schema in 0002). The grant
    # is schema-wide but only applies to tables that existed then.
    op.execute("GRANT SELECT, INSERT, UPDATE ON payment.projection_outbox TO app_payment_rw")


def downgrade() -> None:
    op.execute("REVOKE ALL ON payment.projection_outbox FROM app_payment_rw")
    op.execute("DROP INDEX IF EXISTS payment.ix_projection_outbox_pending")
    op.drop_index("ix_projection_outbox_tenant_id", "projection_outbox", schema="payment")
    op.drop_table("projection_outbox", schema="payment")

    op.drop_column("subscriptions", "provider_event_at", schema="payment")
    op.drop_column("billing_events", "provider_event_at", schema="payment")
    for schema, table in _PROVIDER_COLUMN_TABLES:
        op.drop_column(table, "provider", schema=schema)
    for schema, table, old, new in _PROVIDER_RENAMES:
        op.alter_column(table, new, new_column_name=old, schema=schema)

    for schema, table in (("restaurant", "users"), ("customer", "customers")):
        op.drop_column(table, "tokens_valid_from", schema=schema)

    op.drop_constraint(
        "uq_identity_links_provider_local", "identity_links", schema="restaurant"
    )
    op.drop_constraint(
        "uq_identity_links_provider_subject", "identity_links", schema="restaurant"
    )
    op.drop_index("ix_identity_links_local_id", "identity_links", schema="restaurant")
    op.drop_index("ix_identity_links_tenant_id", "identity_links", schema="restaurant")
    op.drop_table("identity_links", schema="restaurant")
