#!/usr/bin/env sh
set -eu

api_url="$E2E_API_URL"
api_port="$E2E_API_PORT"
cd ../api
runner="${E2E_API_RUNNER:-uv run }"
run() { sh -c "$runner$1"; }
run "alembic upgrade head"
run "python tests/e2e_seed.py"
run "celery -A app.worker.celery_app worker --loglevel=WARNING --pool=solo -n e2e-media@%h" &
worker_pid=$!
run "uvicorn app.main:app --host 127.0.0.1 --port $api_port" &
api_pid=$!
cleanup() { kill "$worker_pid" "$api_pid" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
attempt=0
until response=$(curl -fsS "$api_url/health" 2>/dev/null) && printf '%s' "$response" | grep -q '"service":"fanbackstage-api"'; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 60 ]; then
    echo "FanBackstage API did not identify itself at $api_url/health" >&2
    exit 1
  fi
  sleep 0.25
done
response=$(curl -fsS "$api_url/ready")
printf '%s' "$response" | grep -q '"service":"fanbackstage-api"' || { echo "Unexpected readiness response at $api_url" >&2; exit 1; }
wait "$api_pid"
