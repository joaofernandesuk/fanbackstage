# Phase 0 — Foundation Specification

This is the implementation specification for the first Codex development phase. Phase 0 builds the engineering foundation only. It must **not** implement creator monetisation, paid content, marketplace, subscriptions, full streaming, group contracts or payments yet.

## 1. Mandatory reading before implementation

Codex must read in this order:

1. `AGENTS.md`
2. `PROJECT.md`
3. `PRODUCT.md`
4. `ARCHITECTURE.md`
5. `TECH_STACK.md`
6. `DATA_MODEL.md`
7. `PERMISSIONS.md`
8. `TRUST_AND_SAFETY.md`
9. `ROADMAP.md`
10. this file

If documents conflict, stop implementation of the conflicting portion and choose the stricter invariant; record the conflict clearly rather than inventing a new product rule.

## 2. Phase goal

At completion, the repository must be a clean, runnable, tested platform skeleton where future domains can be added without refactoring the foundation.

Phase 0 establishes:
- monorepo structure
- Next.js web application
- FastAPI API
- PostgreSQL
- SQLAlchemy 2
- Alembic
- Redis
- Celery worker
- configuration/secrets pattern
- account/authentication foundation
- role/permission foundation
- audit-event skeleton
- health/readiness endpoints
- API conventions
- structured logging/correlation IDs
- test infrastructure
- Docker Compose development environment
- CI pipeline
- LiveKit development/infrastructure scaffold only
- FFmpeg/media-worker capability scaffold only

## 3. Non-goals

Do **not** implement in Phase 0:
- creator profile product UI beyond minimal placeholder/role readiness
- subscription products
- promotions
- PPV
- wallet balances
- financial ledger entries beyond optional interface/entity placeholders specifically required by architecture
- payment-provider integrations
- payouts
- videos/galleries production pipeline
- Stories product features
- full creator studio
- live rooms product logic
- tipping
- private-session billing
- group/agency membership/contracts
- marketplace
- referrals/affiliates
- featuring purchases
- recommendation algorithms

Do not create speculative tables for every future domain. Create only the foundation entities needed now and let later phases add their intentional migrations.

## 4. Repository structure

Target:

```text
/
  AGENTS.md
  README.md
  docs/
    PROJECT.md
    PRODUCT.md
    ARCHITECTURE.md
    TECH_STACK.md
    DATA_MODEL.md
    PERMISSIONS.md
    ...
  apps/
    api/
      app/
      tests/
      alembic/
      pyproject.toml
    web/
      src/
      tests/
      package.json
    worker/
  packages/
    ui/
    contracts/
  infra/
    docker/
    livekit/
    observability/
  scripts/
  .env.example
  docker-compose.yml
```

Codex may adjust low-level folder names for framework conventions, but domain boundaries and responsibilities must remain clear.

## 5. Backend foundation

Create FastAPI application with:
- `/api/v1` router root
- dependency/configuration pattern
- async database session management
- explicit application startup/shutdown/lifespan
- structured error responses
- request/correlation ID middleware
- logging configuration
- security headers/CORS configuration appropriate to environment
- health and readiness routes

Minimum endpoints:

```text
GET /health
GET /ready
GET /api/v1/me
POST /api/v1/auth/register
POST /api/v1/auth/login
POST /api/v1/auth/logout
POST /api/v1/auth/refresh   (only if token strategy uses explicit refresh endpoint)
POST /api/v1/auth/forgot-password
POST /api/v1/auth/reset-password
POST /api/v1/auth/verify-email
GET  /api/v1/sessions
DELETE /api/v1/sessions/{session_id}
```

Do not expose password hashes, reset tokens, verification tokens or session secrets through schemas/logging.

## 6. Authentication model

Create a secure account-centric foundation.

Recommended initial entities:

```text
User
UserEmail / verified email state (or equivalent)
UserCredential (if kept separate)
UserSession
EmailVerificationToken
PasswordResetToken
Role
RoleAssignment
Permission / RolePermission (if implementing DB-backed permissions now)
AuditEvent
```

Simpler permission catalog code + DB role assignments is acceptable if documented and extensible.

Requirements:
- normalized unique email handling
- secure modern password hashing
- session/token revocation
- expiration
- refresh/session rotation if applicable
- HTTP-only secure cookie strategy for the web application where practical
- configurable cookie security for local HTTPS/non-HTTPS development
- CSRF design documented/implemented where cookie authentication requires it
- login/register/reset rate-limit hook or implementation
- password reset token is single-use and expiring
- email verification token is single-use and expiring
- logout invalidates the active server-side session
- revoke-other-sessions capability supported by the model/service even if UI is minimal
- auth events written to audit/security log where appropriate

MFA does not need a finished user flow in Phase 0, but the design must not block adding TOTP/passkeys later.

## 7. Initial roles

Seed or define:

```text
viewer
creator
manager
moderator
admin
super_admin
```

A user may have multiple roles.

Do not make role name checks the final authorization architecture. Create a policy/permission layer that can later incorporate creator ownership, group grants and scoped admin permissions.

## 8. Permission foundation

Create a centralized permission-check interface, for example conceptually:

```python
authorize(actor, permission, resource=None)
```

Initial permissions can include:

```text
account.self.read
account.self.update
admin.access
moderation.access
creator.access
manager.access
```

Later phases extend this catalog. Routes/services must not duplicate ad-hoc permission logic.

## 9. Database and migration requirements

- PostgreSQL only for canonical development/integration DB; SQLite is not an acceptable substitute for integration tests.
- SQLAlchemy 2 typed mappings.
- Alembic initialized and working.
- Initial migration creates only Phase 0 schema.
- Migration downgrade should work for Phase 0 where practical.
- Unique/check/index constraints intentional.
- UTC timestamps.
- UUID-compatible primary keys.
- created_at/updated_at semantics consistent.
- soft-delete only where required; do not add blanket soft deletion to every table.

CI should run Alembic upgrade on a fresh PostgreSQL database.

## 10. Audit skeleton

`AuditEvent` should support at minimum:
- event ID
- event type
- actor user ID nullable for system event
- target/resource type
- target/resource ID nullable
- request/correlation ID
- IP/network metadata where policy permits
- user-agent metadata where policy permits
- structured metadata
- created timestamp

Audit records are append-oriented. Provide a service API; do not let unrelated modules directly construct arbitrary audit rows everywhere.

Phase 0 events should include examples such as:
- account.registered
- auth.login_succeeded
- auth.login_failed (mind sensitive-data/noise controls)
- auth.logged_out
- auth.password_reset_requested
- auth.password_reset_completed
- auth.email_verified
- auth.session_revoked
- role.assigned / role.removed for privileged/admin operations

## 11. Redis

Wire Redis and provide:
- connection lifecycle
- cache abstraction foundation
- rate-limit primitive/interface
- transient pub/sub abstraction if useful

Do not make Redis mandatory for correctness of durable account state.

## 12. Celery worker

Create a worker application and at least one harmless test task used by integration/development checks.

Provide queue naming/routing foundation for future:

```text
default
media
notifications
moderation
analytics
financial
scheduled
```

Do not implement business queues fully yet.

## 13. Web application

Next.js + React + TypeScript strict.

Minimum UI:
- application shell
- public landing placeholder
- register
- login
- verify-email result/view
- forgot/reset password
- authenticated account page
- session management view
- permission-aware placeholder navigation for creator/manager/admin areas
- consistent error/loading states

Do not build the finished adult-facing visual design yet. Build a clean design-system foundation that can be reskinned without changing logic.

## 14. API client/contracts

Create a single API-access layer in the web app.

Requirements:
- base URL/config from environment
- credentials/cookie handling
- typed request/response shape
- standardized API error parsing
- no scattered `fetch('/api/...')` calls throughout components

If OpenAPI code generation is introduced, keep generated code isolated and reproducible.

## 15. Design system foundation

Create reusable primitives sufficient for Phase 0:
- Button
- Input
- FormField
- Alert
- Modal/Dialog
- Dropdown/Menu where needed
- Card/Surface
- Avatar placeholder
- Badge
- Spinner/Skeleton

Meet basic accessibility expectations: labels, focus states, keyboard interaction and meaningful error association.

## 16. LiveKit scaffold

Phase 0 does **not** implement the product streaming features.

It should add:
- `infra/livekit/` configuration/example
- local LiveKit service in Compose if reliable for development
- documented environment variables
- backend `StreamingProvider` interface or clear placeholder boundary
- LiveKit adapter skeleton only if it does not create speculative product tables
- connectivity/health documentation

Do not expose a public endpoint that lets arbitrary users mint unrestricted room tokens.

TURN configuration may be documented/scaffolded, with production implementation completed before streaming Phase 7.

## 17. FFmpeg/media scaffold

Phase 0 should:
- ensure media workers can detect/call FFmpeg in their runtime image
- provide a simple version/health check
- define the future media-job boundary

Do not build upload/transcoding/content tables until Phase 2.

## 18. Observability

Implement:
- structured logs
- correlation/request IDs
- environment/service/version fields
- OpenTelemetry-ready initialization or baseline tracing where low-friction
- exception logging without leaking secrets

Health endpoints must differentiate process liveness from dependency readiness.

Example:

```text
/health -> API process alive
/ready  -> DB/required dependencies reachable
```

## 19. Configuration

Use typed settings.

Provide `.env.example` covering:
- environment
- app URLs
- database URL
- Redis URL
- auth secrets/keys
- cookie configuration
- email adapter placeholder/dev transport
- S3-compatible dev config if included
- LiveKit URL/key/secret for local dev
- observability settings

Production must fail safely when critical secrets are absent. No committed default production secrets.

## 20. Local developer experience

Target developer commands should be simple, for example:

```text
make dev
make test
make lint
make migrate
make migration name="..."
make down
```

Equivalent scripts are acceptable, but document one canonical workflow.

A new developer should be able to clone, copy `.env.example`, start dependencies/application, migrate DB and run tests without reverse-engineering the repo.

## 21. CI acceptance

CI must run at least:

Backend:
- formatting/lint
- type/static checks chosen by project
- pytest
- fresh PostgreSQL + Alembic upgrade

Frontend:
- lint
- TypeScript check
- tests
- production build

Repository:
- no committed secrets check where practical
- dependency lockfiles present

## 22. Security acceptance

Tests or verifiable behavior must demonstrate:
- unauthenticated `/me` is rejected
- login with valid credentials works
- invalid login does not reveal whether sensitive internal details exist
- logout/revocation prevents continued session use
- reset token expires/is single-use
- verification token expires/is single-use
- ordinary viewer cannot access admin-only placeholder route/API
- multiple roles can be assigned without duplicating user identity
- privileged role changes emit an audit event
- API does not trust a client-supplied role claim

## 23. Data/privacy acceptance

- password hashes never serialize
- reset/verification raw secrets are not stored/logged in recoverable form where a hashed-token design is practical
- API logs avoid credentials/auth cookies
- sensitive config does not appear in `/health` or error payloads

## 24. Test strategy

Use test factories/fixtures rather than relying on fixture order or a pre-seeded developer database.

Create at least:
- service/unit auth tests
- API auth/session integration tests
- permissions tests
- audit tests
- Alembic fresh-install test
- Redis/worker smoke test
- minimal Playwright authentication smoke path

## 25. Documentation deliverables

Phase 0 implementation must update/create:
- root README developer startup section
- architecture notes for actual choices
- environment variable reference
- migration workflow
- test workflow
- auth/session design note
- any ADR required for a meaningful deviation from this specification

## 26. Definition of done

Phase 0 is complete only when:

1. Repository starts locally from documented commands.
2. Web and API run successfully.
3. PostgreSQL migration applies to an empty database.
4. Redis and Celery worker are healthy.
5. User can register/login/logout/reset/verify email using development delivery flow.
6. Sessions are revocable.
7. Multiple roles and centralized permission checks work.
8. Audit-event service records critical auth/admin foundation events.
9. CI passes backend/frontend/build/migration checks.
10. No later-phase business feature has been prematurely implemented.
11. LiveKit and FFmpeg boundaries are scaffolded/documented without fake product logic.
12. Tests cover the Phase 0 security/business invariants.

## 27. Codex execution instruction

Implement Phase 0 in bounded commits/steps. Before changing a schema or architectural boundary, explain the intended change in the working notes and verify it against the authoritative documentation.

Do not generate one enormous unreviewable migration or one giant application module. Keep the system runnable throughout the phase.
