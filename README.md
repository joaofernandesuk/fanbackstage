# FanBackstage — Get closer. Go backstage.

FanBackstage.com is a modular creator entertainment platform. This repository currently implements **Phase 0 only**: identity, authentication, server-side sessions, permissions, audit foundation, local infrastructure and the web application shell.

## Start locally

1. Copy `.env.example` to `.env` and replace the development session secret for shared environments.
2. Install [uv](https://docs.astral.sh/uv/) and pnpm, then run `make deps`.
3. Run `make dev` to start PostgreSQL, Redis, Mailpit and the local LiveKit scaffold. Mailpit is available at `http://localhost:8025` by default; it receives development verification and password-reset emails.
4. Run `make migrate`, then `make api` and `make web` in separate terminals.

The web app is at `http://localhost:3000`; the API is at `http://localhost:8000`; API documentation is at `/docs`.

## Validation

Run `make lint` and `make test`. CI also runs a production web build and a fresh Alembic migration check against PostgreSQL.

## Architecture

The API is the source of truth for permissions and future business rules. Authentication uses expiring opaque HTTP-only session cookies backed by revocable database records. See [Phase 0 implementation notes](docs/PHASE_0_IMPLEMENTATION_NOTES.md) and the preserved [project documentation](docs/README.md).

## Scope

No creator profiles, content, streaming rooms, commerce, payments, subscriptions, messaging, marketplace, or financial ledger product logic is included yet. Those domains remain roadmap phases and must retain the documented invariants.
