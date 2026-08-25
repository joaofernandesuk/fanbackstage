#!/usr/bin/env sh
set -eu

if [ "${FANBACKSTAGE_ENVIRONMENT:-development}" != "development" ]; then
  echo "dev-backup only permits the disposable development environment" >&2
  exit 1
fi
mkdir -p .fanbackstage-dev/backups
backup_path=".fanbackstage-dev/backups/fanbackstage-$(date -u +%Y%m%dT%H%M%SZ).sql"
docker compose exec -T postgres pg_dump -U fanbackstage -d fanbackstage >"$backup_path"
echo "Local database backup written to $backup_path"
