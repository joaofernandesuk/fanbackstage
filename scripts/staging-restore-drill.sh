#!/usr/bin/env sh
set -eu

confirmation="RESTORE-INTO-ISOLATED-STAGING-VALIDATION"
if [ "${FANBACKSTAGE_ENVIRONMENT:-}" != "staging" ]; then
  echo "Refusing restore drill outside FANBACKSTAGE_ENVIRONMENT=staging" >&2
  exit 2
fi
if [ "$#" -ne 3 ] || [ "$3" != "$confirmation" ]; then
  echo "Usage: staging-restore-drill.sh BACKUP.dump TARGET_DATABASE_URL $confirmation" >&2
  exit 2
fi
target_without_query=${2%%\?*}
target_database=${target_without_query##*/}
case "$target_database" in
  *restore*|*validation*) ;;
  *) echo "Target database name must visibly contain restore or validation" >&2; exit 2 ;;
esac
if [ ! -f "$1" ] || [ ! -f "$1.metadata" ] || [ ! -f "$1.sha256" ]; then
  echo "Backup, version metadata, and adjacent SHA-256 manifest are required" >&2
  exit 2
fi
for command_name in psql pg_restore sha256sum; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "$command_name is required" >&2
    exit 2
  }
done
sha256sum --check "$1.sha256"
source_server_major=$(sed -n 's/^source_server_major=//p' "$1.metadata")
dump_client_major=$(sed -n 's/^pg_dump_major=//p' "$1.metadata")
target_version_num=$(psql "$2" -Atc "SHOW server_version_num")
target_server_major=$((target_version_num / 10000))
restore_client_major=$(pg_restore --version | sed -n 's/^pg_restore (PostgreSQL) \([0-9][0-9]*\)\..*/\1/p')
for major in \
  "$source_server_major" \
  "$dump_client_major" \
  "$target_server_major" \
  "$restore_client_major"; do
  case "$major" in
    ""|*[!0-9]*)
      echo "Unable to determine PostgreSQL backup/restore major versions" >&2
      exit 2
      ;;
  esac
done
if [ "$source_server_major" -ne "$dump_client_major" ] \
  || [ "$source_server_major" -ne "$target_server_major" ] \
  || [ "$target_server_major" -ne "$restore_client_major" ]; then
  echo "Backup source, pg_dump, restore target, and pg_restore major versions must match" >&2
  exit 2
fi
pg_restore --exit-on-error --clean --if-exists --no-owner --no-acl --dbname "$2" "$1"
echo "Restore loaded into the isolated validation target; run migrations, readiness, and smoke checks."
