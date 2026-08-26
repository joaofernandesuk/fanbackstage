#!/usr/bin/env sh
set -eu

api_url="$E2E_API_URL"
api_port="$E2E_API_PORT"
beat_schedule="${TMPDIR:-/tmp}/fanbackstage-e2e-celerybeat-${api_port}-$$"
cd ../api
runner="${E2E_API_RUNNER:-uv run }"
# Replace the helper shell with the requested process.  This keeps the captured
# worker/API PIDs authoritative so Playwright cannot leave a stale Celery worker
# consuming a later run's notification jobs.
run() { sh -c "exec $runner$1"; }
run "alembic upgrade head"
run "python tests/e2e_seed.py"
# The private-live E2E exercises provider-authoritative presence repair when a
# webhook is delayed or unavailable. Run the production beat schedule inside
# this isolated one-process worker so the bounded Playwright polls observe the
# same reconciliation path used after a missed callback.
run "celery -A app.worker.celery_app worker --beat --schedule=$beat_schedule --loglevel=WARNING --pool=solo -n e2e-${api_port}-$$@%h" &
worker_pid=$!
# LiveKit runs in Docker and delivers signed lifecycle callbacks through the
# host-gateway address.  Binding only loopback makes the API readiness probe
# pass while silently dropping those provider callbacks in Linux CI.
run "uvicorn app.main:app --host 0.0.0.0 --port $api_port" &
api_pid=$!
cleanup() {
  trap - EXIT INT TERM
  kill "$worker_pid" "$api_pid" 2>/dev/null || true
  attempts=0
  while { kill -0 "$worker_pid" 2>/dev/null || kill -0 "$api_pid" 2>/dev/null; } && [ "$attempts" -lt 20 ]; do
    attempts=$((attempts + 1))
    sleep 0.1
  done
  kill -9 "$worker_pid" "$api_pid" 2>/dev/null || true
  wait "$worker_pid" "$api_pid" 2>/dev/null || true
  # The embedded beat child can flush its shelve file just after its worker exits.
  sleep 0.2
  rm -f "$beat_schedule" "$beat_schedule.db" "$beat_schedule.bak" "$beat_schedule.dat" "$beat_schedule.dir"
}
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
