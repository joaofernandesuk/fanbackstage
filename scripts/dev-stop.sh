#!/usr/bin/env sh
set -eu

state_dir=".fanbackstage-dev"
if [ -d "$state_dir" ]; then
  for pid_file in "$state_dir"/*.pid; do
    [ -f "$pid_file" ] || continue
  pid=$(<"$pid_file")
    if kill -0 "$pid" 2>/dev/null; then kill "$pid"; fi
    rm "$pid_file"
  done
fi
