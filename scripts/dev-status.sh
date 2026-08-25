#!/usr/bin/env sh
set -eu

docker compose ps
docker compose exec -T postgres pg_isready -U fanbackstage -d fanbackstage
docker compose exec -T redis redis-cli ping | grep -qx PONG
curl -fsS http://127.0.0.1:${FANBACKSTAGE_MAILPIT_UI_PORT:-8025}/api/v1/info >/dev/null
curl -fsS http://127.0.0.1:${FANBACKSTAGE_MINIO_PORT:-9000}/minio/health/live >/dev/null
curl -fsS http://127.0.0.1:8000/ready
curl -fsS http://127.0.0.1:3000 >/dev/null
(cd apps/api && uv run alembic current | grep -q '(head)')
(cd apps/api && uv run celery -A app.worker.celery_app inspect ping --timeout=5 | grep -q pong)
echo "FanBackstage local runtime is healthy."
