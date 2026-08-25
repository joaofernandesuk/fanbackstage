#!/usr/bin/env sh
set -eu

mkdir -p .fanbackstage-dev/backups
backup_path=".fanbackstage-dev/backups/fanbackstage-$(date -u +%Y%m%dT%H%M%SZ).sql"
docker compose -f docker-compose.dev.yml exec -T postgres pg_dump -U fanbackstage -d fanbackstage >"$backup_path"
echo "Local database backup written to $backup_path"
