"""QuickBite — PostgreSQL Row-Level Security policies.

Defines RLS policies for all tenant-scoped tables.
Every table with tenant_id gets an RLS policy that filters
by the session-level app.tenant_id setting.
"""
