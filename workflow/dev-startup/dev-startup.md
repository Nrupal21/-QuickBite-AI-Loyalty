---
description: Start the full QuickBite development environment — Docker, migrations, seed data
slash_command: /dev-startup
---

## Start Development Environment

1. Start the full Docker stack // turbo
   `docker-compose up -d`

2. Wait for PostgreSQL to be ready
   `docker-compose exec db pg_isready -U postgres -d quickbite`

3. Apply all pending Alembic migrations // turbo
   `alembic upgrade head`

4. Seed roles and subscription plans // turbo
   `python scripts/seed_roles.py && python scripts/seed_plans.py`

5. Verify FastAPI health endpoint
   `curl -s http://localhost:8000/health/live | python3 -m json.tool`

6. Verify Redis is reachable
   `docker-compose exec redis redis-cli ping`

7. Show running services
   `docker-compose ps`

## If Starting Fresh (clean slate)
```bash
docker-compose down -v       # ⚠️ destroys all local data
docker-compose up -d
alembic upgrade head
python scripts/seed_roles.py
python scripts/seed_plans.py
```

## Stop the Environment
```bash
docker-compose down          # stop containers (keeps data volumes)
docker-compose down -v       # stop + destroy all volumes (fresh start)
```
