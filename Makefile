.DEFAULT_GOAL := help

help:
	@echo "FanBackstage: make deps | dev | test | lint | migrate | worker"
deps:
	cd apps/api && uv sync --all-groups
	cd apps/web && pnpm install --frozen-lockfile=false
dev:
	docker compose up -d postgres redis livekit
	@echo "Run 'make api' and 'make web' in separate terminals."
api:
	cd apps/api && uv run uvicorn app.main:app --reload --port 8000
web:
	cd apps/web && pnpm dev
worker:
	cd apps/api && uv run celery -A app.worker.celery_app worker --loglevel=INFO
migrate:
	cd apps/api && uv run alembic upgrade head
test:
	cd apps/api && FANBACKSTAGE_ENVIRONMENT=test FANBACKSTAGE_DATABASE_URL=postgresql+asyncpg://fanbackstage:fanbackstage@localhost:5432/fanbackstage FANBACKSTAGE_REDIS_URL=redis://localhost:6379/1 uv run pytest
	cd apps/web && pnpm test
lint:
	cd apps/api && uv run ruff check . && uv run ruff format --check .
	cd apps/web && pnpm lint && pnpm typecheck
down:
	docker compose down
