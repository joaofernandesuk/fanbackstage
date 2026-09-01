#!/usr/bin/env sh
set -eu

docker compose -f docker-compose.dev.yml up -d --build
./scripts/livekit-local.sh start
./scripts/livekit-control-local.sh start
echo "fanbackstage-dev is starting. Run 'make dev-status', then 'make demo-seed'."
