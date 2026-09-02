#!/usr/bin/env sh
set -eu
umask 077

if [ "${FANBACKSTAGE_ENVIRONMENT:-}" != "staging" ]; then
  echo "Refusing backup outside FANBACKSTAGE_ENVIRONMENT=staging" >&2
  exit 2
fi
if [ -z "${FANBACKSTAGE_DATABASE_URL_SYNC:-}" ]; then
  echo "Set FANBACKSTAGE_DATABASE_URL_SYNC to the operator-only PostgreSQL backup URL" >&2
  exit 2
fi
if [ "$#" -ne 1 ] || [ ! -d "$1" ]; then
  echo "Usage: staging-backup.sh EXISTING_PRIVATE_OUTPUT_DIRECTORY" >&2
  exit 2
fi
for command_name in psql pg_dump sha256sum; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "$command_name is required" >&2
    exit 2
  }
done

server_version_num=$(psql "$FANBACKSTAGE_DATABASE_URL_SYNC" -Atc "SHOW server_version_num")
server_major=$((server_version_num / 10000))
client_major=$(pg_dump --version | sed -n 's/^pg_dump (PostgreSQL) \([0-9][0-9]*\)\..*/\1/p')
for major in "$server_major" "$client_major"; do
  case "$major" in
    ""|*[!0-9]*)
      echo "Unable to determine PostgreSQL server/client major versions" >&2
      exit 2
      ;;
  esac
done
if [ "$server_major" -ne "$client_major" ]; then
  echo "pg_dump major $client_major must match source PostgreSQL major $server_major" >&2
  exit 2
fi

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
output="$1/fanbackstage-staging-$timestamp.dump"
pg_dump --format=custom --no-owner --no-acl --file "$output" "$FANBACKSTAGE_DATABASE_URL_SYNC"
metadata="$output.metadata"
{
  echo "source_server_major=$server_major"
  echo "pg_dump_major=$client_major"
} > "$metadata"
sha256sum "$output" "$metadata" > "$output.sha256"
echo "Encrypted storage upload is an operator responsibility: $output"
