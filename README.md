# FanBackstage — Get closer. Go backstage.

FanBackstage.com is a modular creator entertainment platform. This repository implements the identity, creator-profile, and in-progress **Phase 2 content and media foundation**.

## Start locally

1. Copy `.env.example` to `.env` and replace the development session secret for shared environments.
2. Install [uv](https://docs.astral.sh/uv/) and pnpm, then run `make deps`.
3. Run `make dev` to start PostgreSQL, Redis, Mailpit, MinIO and the local LiveKit scaffold. Mailpit is available at `http://localhost:8025` by default; it receives development verification and password-reset emails.
4. Run `make migrate`, then `make api` and `make web` in separate terminals.

The web app is at `http://localhost:3000`; the API is at `http://localhost:8000`; API documentation is at `/docs`.

## Validation

Run `make lint` and `make test`. CI also runs a production web build and a fresh Alembic migration check against PostgreSQL.

## Architecture

The API is the source of truth for permissions and future business rules. Authentication uses expiring opaque HTTP-only session cookies backed by revocable database records. See [Phase 0 implementation notes](docs/PHASE_0_IMPLEMENTATION_NOTES.md) and the preserved [project documentation](docs/README.md).

## Scope

Phase 2 adds private S3-compatible creator uploads, image/video processing, derivative delivery, galleries, standalone videos, content lifecycle, preview selection, and server-authorized access policies. Payments, subscription billing, PPV checkout, feed, stories, messaging, marketplace, and financial ledger product logic remain later phases.
