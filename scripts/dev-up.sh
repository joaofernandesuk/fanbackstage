#!/usr/bin/env sh
set -eu

docker compose -f docker-compose.dev.yml up -d --build
echo "fanbackstage-dev is starting in Docker Desktop. Run 'make dev-status', then 'make demo-seed'."
