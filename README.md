# FanBackstage — Get closer. Go backstage.

Phase 11 adds server-authoritative discovery at `/discover` and `/search`. See [Discovery](docs/DISCOVERY.md) for the safe-public projections, ranking, and limits.

FanBackstage.com is a modular creator entertainment platform. This repository implements the identity, creator-profile, content/media foundation, and **Phase 3 financial core**.

## Start locally

1. Copy `.env.example` to `.env` and replace the development session secret for shared environments.
2. Install [uv](https://docs.astral.sh/uv/) and pnpm, then run `make deps`.
3. Run `make dev` to start PostgreSQL, Redis, Mailpit, MinIO and the local LiveKit scaffold. Mailpit is available at `http://localhost:8025` by default; it receives development verification and password-reset emails.
4. Run `make migrate`, then `make api`, `make worker`, and `make web` in separate terminals. The worker and MinIO are required for upload/processing flows.

Development pins MinIO to `RELEASE.2025-09-07T16-13-09Z`. Its private bucket is never made public: direct browser PUTs are restricted with MinIO's server-level CORS allowlist (`http://localhost:3000` and the Playwright origins `http://127.0.0.1:31000` and `http://127.0.0.1:38181`). CORS only permits the browser request; it grants no object-read permission.

The web app is at `http://localhost:3000`; the API is at `http://localhost:8000`; API documentation is at `/docs`.

## Validation

Run `make lint` and `make test`. Run `make e2e` for the real local media journey; it starts the full dependency stack and launches the API, worker, and browser application through Playwright. CI also runs a production web build, real-stack browser journeys, and fresh reversible Alembic migration checks against PostgreSQL.

## Architecture

The API is the source of truth for permissions and future business rules. Authentication uses expiring opaque HTTP-only session cookies backed by revocable database records. See [Phase 0 implementation notes](docs/PHASE_0_IMPLEMENTATION_NOTES.md) and the preserved [project documentation](docs/README.md).

## Scope

Phase 3 adds private PPV pricing, signed development payment webhooks, idempotent PPV purchase settlement, append-only double-entry ledger records, commission snapshots, creator pending/available balances, and auditable full refunds that revoke access. Subscriptions, promotions, feed, stories, messaging, marketplace, and payout execution remain later phases.
