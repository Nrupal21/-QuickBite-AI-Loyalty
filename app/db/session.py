"""QuickBite — Async SQLAlchemy engine + get_db() session factory.

Uses asyncpg driver. Sets app.tenant_id on every session
via SQLAlchemy event listener for RLS enforcement.
"""
