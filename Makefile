.DEFAULT_GOAL := help

help:
	@echo "FanBackstage: make deps | dev | dev-status | dev-stop | dev-reset | demo-seed | demo-validate | smoke | dev-backup | test | lint | migrate | e2e"
deps:
	cd apps/api && uv sync --all-groups
	cd apps/web && pnpm install --frozen-lockfile=false
dev:
	./scripts/dev-up.sh
dev-status:
	./scripts/dev-status.sh
livekit-status:
	./scripts/livekit-local.sh status
livekit-smoke:
	node scripts/livekit-local-smoke.mjs
dev-stop:
	./scripts/dev-stop.sh
dev-reset:
	./scripts/dev-reset.sh
demo-seed:
	docker compose -f docker-compose.dev.yml exec -T api python -m app.seed.demo
demo-validate:
	docker compose -f docker-compose.dev.yml exec -T api python -m app.seed.validate
smoke:
	./scripts/smoke.sh
dev-backup:
	./scripts/dev-backup.sh
api:
	cd apps/api && uv run uvicorn app.main:app --reload --port 8000 --no-access-log --no-proxy-headers
web:
	cd apps/web && pnpm dev
worker:
	cd apps/api && uv run celery -A app.worker.celery_app worker --loglevel=INFO
e2e:
	cd apps/web && pnpm test:e2e
migrate:
	cd apps/api && uv run alembic upgrade head
test:
	cd apps/api && FANBACKSTAGE_ENVIRONMENT=test FANBACKSTAGE_DATABASE_URL=postgresql+asyncpg://fanbackstage:fanbackstage@localhost:5432/fanbackstage FANBACKSTAGE_REDIS_URL=redis://localhost:6379/1 uv run pytest
	cd apps/web && pnpm test
lint:
	cd apps/api && uv run ruff check . && uv run ruff format --check .
	cd apps/web && pnpm lint && pnpm typecheck
phase14-format:
	@files="$$(git diff --name-only --diff-filter=ACMR origin/codex/phase-14-analytics-bi...HEAD -- '*.py'; git diff --name-only -- '*.py')"; \
	if [ -n "$$files" ]; then apps/api/.venv/bin/ruff format --check $$files; fi
down:
	docker compose down
