#!/usr/bin/env sh
set -eu

compose="docker compose -f docker-compose.dev.yml"
$compose ps
$compose exec -T postgres pg_isready -U fanbackstage -d fanbackstage
$compose exec -T redis redis-cli ping | grep -qx PONG
curl -fsS http://127.0.0.1:18035/api/v1/info >/dev/null
curl -fsS http://127.0.0.1:19010/minio/health/live >/dev/null
curl -fsS http://127.0.0.1:18000/ready >/dev/null
curl -fsS http://127.0.0.1:13000 >/dev/null
$compose exec -T api alembic current | grep -q '(head)'
$compose exec -T worker celery -A app.worker.celery_app inspect ping --timeout=5 | grep -q pong
./scripts/livekit-local.sh status
./scripts/livekit-control-local.sh status
echo "fanbackstage-dev is healthy."
