# FanBackstage Phase 0 implementation notes

## Decisions

- Authentication uses opaque, random server-side session secrets in HTTP-only, `SameSite=Lax` cookies. Database-backed sessions support expiry and revocation; cookie authentication remains CSRF-sensitive for future unsafe cross-origin flows, so the current CORS policy permits only the configured web origin and future cross-site mutation flows must add an explicit CSRF token.
- Raw reset and email-verification secrets are issued only at delivery time and persisted as SHA-256 hashes. An `EmailProvider` boundary sends development links through SMTP/Mailpit; no normal API response contains a raw token.
- PostgreSQL is authoritative; Redis is used only for readiness and Celery transport. LiveKit has no public token endpoint in Phase 0. FFmpeg/media processing remains a worker boundary for Phase 2.
- Local Compose ports are configurable through `FANBACKSTAGE_POSTGRES_PORT` and `FANBACKSTAGE_REDIS_PORT`; use alternate host ports when a developer already has local services running.
- Sensitive account commands use a Redis-backed, IP-and-subject rate-limit primitive. It is deliberately isolated from durable account state. Production must provision Redis before serving these commands.
- The migration runner uses the synchronous `psycopg` driver while application traffic uses async `asyncpg`; both operate against the same PostgreSQL schema.
- Celery declares future queue names but only the harmless `health_ping` task is executable in Phase 0. Task retries must remain idempotent as future work is added.
- CSRF posture: the intended deployment is same-site web/API with HTTP-only `SameSite=Lax` cookies and a restricted CORS allowlist. Before any cross-site unsafe request is supported in production, implement explicit CSRF-token and origin protection; do not relax the current policy first.
- The reviewed migration contains only identity, session, security-token, role-assignment and append-oriented audit tables. No later-phase product or financial tables are introduced.

## Developer workflow

Copy `.env.example` to `.env`, run `make dev`, `make migrate`, then run `make api` and `make web` in separate terminals. `make worker` starts the harmless `health_ping` task worker.
