#!/usr/bin/env sh
set -eu

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi
environment=${FANBACKSTAGE_ENVIRONMENT:-development}
database_url=${FANBACKSTAGE_DATABASE_URL:-postgresql+asyncpg://fanbackstage:fanbackstage@localhost:5432/fanbackstage}
case "$environment:$database_url" in
  development:*localhost*|development:*127.0.0.1*) ;;
  *) echo "dev-reset only permits a local development database" >&2; exit 1 ;;
esac

./scripts/dev-stop.sh
docker compose down -v --remove-orphans
./scripts/dev-up.sh
make demo-seed
