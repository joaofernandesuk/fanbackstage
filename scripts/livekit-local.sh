#!/usr/bin/env sh
set -eu

state_dir=".fanbackstage-dev"
pid_file="$state_dir/livekit.pid"
log_file="$state_dir/livekit.log"

mkdir -p "$state_dir"

is_running() {
  [ -f "$pid_file" ] && kill -0 "$(cat "$pid_file")" 2>/dev/null
}

running_server_pid() {
  lsof -nP -tiTCP:17880 -sTCP:LISTEN 2>/dev/null | head -n 1
}

adopt_running_server() {
  existing_pid="$(running_server_pid)"
  [ -n "$existing_pid" ] || return 1
  if ps -p "$existing_pid" -o command= 2>/dev/null | grep -q "livekit-server"; then
    echo "$existing_pid" >"$pid_file"
    echo "Native LiveKit is already ready at ws://localhost:17880"
    return 0
  fi
  echo "Port 17880 is already in use by a process other than livekit-server" >&2
  exit 1
}

start() {
  if is_running; then
    echo "Native LiveKit is already ready at ws://localhost:17880"
    return 0
  fi
  rm -f "$pid_file"
  if adopt_running_server; then
    return 0
  fi
  if ! command -v livekit-server >/dev/null 2>&1; then
    echo "livekit-server is required for local camera publishing. Install it with: brew install livekit" >&2
    exit 1
  fi
  nohup livekit-server --config infra/livekit/livekit.local.yaml </dev/null >"$log_file" 2>&1 &
  pid=$!
  echo "$pid" >"$pid_file"
  attempts=0
  while ! nc -z 127.0.0.1 17880 >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    if [ "$attempts" -ge 30 ]; then
      kill "$pid" 2>/dev/null || true
      rm -f "$pid_file"
      echo "LiveKit did not become ready; see $log_file" >&2
      exit 1
    fi
    sleep 1
  done
  echo "Native LiveKit is ready at ws://localhost:17880"
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
    if is_running || adopt_running_server; then
      echo "Native LiveKit is running"
    else
      echo "Native LiveKit is not running" >&2
      exit 1
    fi
    ;;
  *) echo "Usage: $0 {start|stop|status}" >&2; exit 2 ;;
esac
