#!/usr/bin/env sh
set -eu

api_url="$E2E_API_URL"
api_port="$E2E_API_PORT"
beat_schedule="${TMPDIR:-/tmp}/fanbackstage-e2e-celerybeat-${api_port}-$$"
worker_host=$(hostname)
core_worker_name="e2e-core-${api_port}-$$@$worker_host"
notification_worker_name="e2e-notifications-${api_port}-$$@$worker_host"
cd ../api
runner="${E2E_API_RUNNER:-uv run }"
# Replace the helper shell with the requested process.  This keeps the captured
# worker/API PIDs authoritative so Playwright cannot leave a stale Celery worker
# consuming a later run's notification jobs.
run() { sh -c "exec $runner$1"; }
run "alembic upgrade head"
run "python tests/e2e_seed.py"
# Keep the production beat schedule on the core worker so private-live and
# lifecycle tests exercise their real reconciliation paths. Notification jobs
# have a dedicated worker so FFmpeg and scheduled tasks cannot starve security
# emails or the release-validation delivery assertions.
sh -c "exec ${runner}celery -A app.worker.celery_app worker --beat --schedule=$beat_schedule --loglevel=WARNING --pool=solo -Q default,media,moderation,analytics,financial,scheduled -n $core_worker_name" &
core_worker_pid=$!
sh -c "exec ${runner}celery -A app.worker.celery_app worker --loglevel=WARNING --pool=solo -Q notifications,notifications_marketing -n $notification_worker_name" &
notification_worker_pid=$!
api_pid=""
cleanup() {
  trap - EXIT INT TERM
  pids="$core_worker_pid $notification_worker_pid"
  if [ -n "$api_pid" ]; then pids="$pids $api_pid"; fi
  kill $pids 2>/dev/null || true
  attempts=0
  while [ "$attempts" -lt 20 ]; do
    alive=0
    for pid in $pids; do
      if kill -0 "$pid" 2>/dev/null; then alive=1; fi
    done
    [ "$alive" -eq 1 ] || break
    attempts=$((attempts + 1))
    sleep 0.1
  done
  kill -9 $pids 2>/dev/null || true
  wait $pids 2>/dev/null || true
  # The embedded beat child can flush its shelve file just after its worker exits.
  sleep 0.2
  rm -f "$beat_schedule" "$beat_schedule.db" "$beat_schedule.bak" "$beat_schedule.dat" "$beat_schedule.dir"
}
trap cleanup EXIT INT TERM
worker_attempt=0
until run "celery -A app.worker.celery_app inspect ping --destination=$core_worker_name --timeout=1" >/dev/null 2>&1 \
  && run "celery -A app.worker.celery_app inspect ping --destination=$notification_worker_name --timeout=1" >/dev/null 2>&1; do
  worker_attempt=$((worker_attempt + 1))
  if [ "$worker_attempt" -ge 30 ]; then
    echo "FanBackstage E2E Celery workers did not become ready" >&2
    exit 1
  fi
  sleep 0.25
done
# LiveKit runs in Docker and delivers signed lifecycle callbacks through the
# host-gateway address. Binding only loopback makes the API readiness probe pass
# while silently dropping those provider callbacks in Linux CI. Start the API
# only after both workers reply so its readiness URL gates the complete stack.
sh -c "exec ${runner}uvicorn app.main:app --host 0.0.0.0 --port $api_port" &
api_pid=$!
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
