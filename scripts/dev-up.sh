#!/usr/bin/env sh
set -eu

state_dir=".fanbackstage-dev"
mkdir -p "$state_dir"
./scripts/dev-stop.sh
docker compose up -d postgres redis mailpit minio livekit

attempt=0
until [ "$(docker compose ps --format json | grep -c '"healthy"' || true)" -ge 4 ]; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    echo "Local dependency health checks did not become ready" >&2
    exit 1
  fi
  sleep 1
done

if command -v uv >/dev/null 2>&1; then
  (cd apps/api && uv run alembic upgrade head)
  (cd apps/api && nohup uv run uvicorn app.main:app --reload --port 8000 >"../../$state_dir/api.log" 2>&1 & echo $! >"../../$state_dir/api.pid")
  (cd apps/api && nohup uv run celery -A app.worker.celery_app worker --beat --loglevel=INFO >"../../$state_dir/worker.log" 2>&1 & echo $! >"../../$state_dir/worker.pid")
else
  test -x apps/api/.venv/bin/alembic || { echo "Install API dependencies with uv sync" >&2; exit 1; }
  (cd apps/api && .venv/bin/alembic upgrade head)
  (cd apps/api && nohup .venv/bin/uvicorn app.main:app --reload --port 8000 >"../../$state_dir/api.log" 2>&1 & echo $! >"../../$state_dir/api.pid")
  (cd apps/api && nohup .venv/bin/celery -A app.worker.celery_app worker --beat --loglevel=INFO >"../../$state_dir/worker.log" 2>&1 & echo $! >"../../$state_dir/worker.pid")
fi
(cd apps/web && nohup pnpm dev >"../../$state_dir/web.log" 2>&1 & echo $! >"../../$state_dir/web.pid")

attempt=0
until curl -fsS http://127.0.0.1:8000/ready >/dev/null && curl -fsS http://127.0.0.1:3000 >/dev/null; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    echo "API or web application failed to become ready; inspect $state_dir/*.log" >&2
    exit 1
  fi
  sleep 1
done
echo "FanBackstage local runtime is ready. Run 'make demo-seed', then 'make dev-status'."
