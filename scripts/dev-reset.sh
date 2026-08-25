#!/usr/bin/env sh
set -eu

docker compose -f docker-compose.dev.yml down -v --remove-orphans
./scripts/dev-up.sh
make demo-seed
