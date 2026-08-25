#!/usr/bin/env sh
set -eu

./scripts/dev-status.sh
curl -fsS http://127.0.0.1:8000/health | grep -q 'fanbackstage-api'
curl -fsS http://127.0.0.1:8000/docs >/dev/null
echo "Local smoke checks passed."
