#!/usr/bin/env sh
set -eu

./scripts/livekit-control-local.sh stop
./scripts/livekit-local.sh stop
docker compose -f docker-compose.dev.yml down -v --remove-orphans
./scripts/dev-up.sh
make demo-seed
