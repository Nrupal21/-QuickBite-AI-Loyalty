# QuickBite AI + Loyalty — dev shortcuts (DOCKER ticket)

.PHONY: dev test shell lint security migrate seed down

dev:            ## Start the full local stack (FastAPI + PostGIS + Redis + Celery)
	docker-compose up -d

down:           ## Stop the stack
	docker-compose down

test:           ## Run the test suite with coverage
	pytest --cov=app --cov-report=term-missing

lint:           ## Ruff lint (must pass before commit)
	ruff check app/ tests/

security:       ## Bandit security scan (run before push)
	bandit -r app/ -ll

shell:          ## Shell inside the web container
	docker-compose exec web sh

migrate:        ## Apply Alembic migrations inside the web container
	docker-compose exec web alembic upgrade head

seed:           ## Seed roles + plans
	docker-compose exec web sh -c "python scripts/seed_roles.py && python scripts/seed_plans.py"
