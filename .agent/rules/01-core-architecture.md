---
trigger: always_on
description: Core QuickBite tech stack and architecture — applied to every task
---

# QuickBite — Core Architecture Rules

## Stack Identity
- Python 3.12, FastAPI 0.111, Pydantic v2, SQLAlchemy 2.x async
- PostgreSQL 16 + PostGIS 3.4, Redis 7, Celery 5.3
- Multi-tenant SaaS — every DB table has a `tenant_id` column with RLS

## Folder Structure (never deviate)
- Routes → `app/api/v1/{domain}/`
- Business logic → `app/services/`
- DB models → `app/db/models/`
- Pydantic schemas → `app/schemas/`
- Celery tasks → `app/tasks/`
- Jinja2 templates → `app/templates/{domain}/`

## Multi-Tenancy — Always Active
- `tenant_id` is set on every authenticated DB session via SQLAlchemy event listener
- RLS is enabled on all 15 tenant-scoped tables
- NEVER query without tenant context — the middleware handles this automatically
- Super Admin uses `BYPASSRLS` DB role (only accessible from `/admin` endpoints)

## Logging
- Always use `structlog` — never `print()` or `logging.basicConfig()`
- NEVER log PII (phone numbers, emails, names) — log hashes only
