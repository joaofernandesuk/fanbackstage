#!/usr/bin/env sh
set -eu

state_dir=".fanbackstage-dev"
pid_file="$state_dir/livekit-control.pid"
log_file="$state_dir/livekit-control.log"
celery_bin="apps/api/.venv/bin/celery"

mkdir -p "$state_dir"

is_running() {
  [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

start() {
  if is_running; then
    echo "Native LiveKit control worker is already running"
    return 0
  fi
  rm -f "$pid_file"
  if [ ! -x "$celery_bin" ]; then
    echo "The API development environment is required. Run: make deps" >&2
    exit 1
  fi
  nohup sh -c '
    cd apps/api
    export FANBACKSTAGE_ENVIRONMENT=development
    export FANBACKSTAGE_DATABASE_URL=postgresql+asyncpg://fanbackstage:fanbackstage@127.0.0.1:15432/fanbackstage
    export FANBACKSTAGE_REDIS_URL=redis://127.0.0.1:16390/0
    export FANBACKSTAGE_LIVEKIT_URL=ws://127.0.0.1:17880
    export FANBACKSTAGE_LIVEKIT_CONTROL_URL=ws://127.0.0.1:17880
    exec .venv/bin/celery -A app.worker.celery_app worker \
      --loglevel=INFO --pool=solo --concurrency=1 \
      -Q live_control -n livekit-control@localhost
  ' </dev/null >"$log_file" 2>&1 &
  worker_pid=$!
  echo "$worker_pid" >"$pid_file"
  attempts=0
  while ! grep -q "livekit-control@localhost ready" "$log_file" 2>/dev/null; do
    attempts=$((attempts + 1))
    if ! kill -0 "$worker_pid" 2>/dev/null || [ "$attempts" -ge 20 ]; then
      kill "$worker_pid" 2>/dev/null || true
      rm -f "$pid_file"
      echo "Native LiveKit control worker did not become ready; see $log_file" >&2
      exit 1
    fi
    sleep 1
  done
  echo "Native LiveKit control worker is ready"
}

stop() {
  if is_running; then
    kill "$(cat "$pid_file")"
  fi
  rm -f "$pid_file"
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  status)
    if is_running; then
      echo "Native LiveKit control worker is running"
    else
      echo "Native LiveKit control worker is not running" >&2
      exit 1
    fi
    ;;
  *) echo "Usage: $0 {start|stop|status}" >&2; exit 2 ;;
esac
