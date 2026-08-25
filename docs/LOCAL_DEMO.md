# Local demo

This data is fictional, harmless, and development-only. `fanbackstage-dev` is the permanent Docker Desktop project for manual testing; it is separate from the disposable `fanbackstage-phase14-release` and `fanbackstage-phase15-release` projects.

From `codex/production-readiness`:

```sh
make dev
make demo-seed
make dev-status
```

Open `http://localhost:13000`, API at `http://localhost:18000`, API documentation at `http://localhost:18000/docs`, Mailpit at `http://localhost:18035`, and MinIO at `http://localhost:19011`. LiveKit listens on `ws://localhost:17880`.

Docker Desktop workflow: open Docker Desktop, locate **fanbackstage-dev**, start the project, wait for API/web/dependency health checks, then open the frontend URL above. Run `make demo-seed` once after a new/reset stack; it is intentionally not automatic on startup. `make dev-status` verifies the project. The fixed development-only ports avoid the occupied host ports and release-validation ranges: PostgreSQL `15432`, Redis `16390`, SMTP `11035`, Mailpit `18035`, MinIO `19010/19011`, API `18000`, web `13000`, and LiveKit `17880/17881/17882`.

All accounts use `fanbackstage-demo-local-only` and must never be reused outside this disposable local environment.

| Role | Email | What to test |
| --- | --- | --- |
| Admin | admin@demo.fanbackstage.local | administration and platform controls |
| Moderator | moderator@demo.fanbackstage.local | ordinary moderation |
| Sensitive reviewer | evidence-moderator@demo.fanbackstage.local | restricted evidence permission via super-admin |
| Manager | manager@demo.fanbackstage.local | manager entry points |
| Fan | subscriber@demo.fanbackstage.local | discovery and subscriptions |
| Fan | ppvbuyer@demo.fanbackstage.local | purchase journey |
| Creator | luna-sparks@demo.fanbackstage.local | established creator profile |
| Creator | skye-live@demo.fanbackstage.local | local streaming setup |

The seed creates authoritative accounts, roles, approved/suspended creator transitions, verified identities, and follow rows. `make dev-reset` is deliberately guarded to a development database URL on localhost and recreates only Compose-owned volumes.

Use `make dev-backup` to create a disposable local PostgreSQL dump under `.fanbackstage-dev/backups/`. `make dev-reset` removes only named `fanbackstage-dev` Compose volumes before rebuilding and reseeding. A restore drill should be performed only against this reset local stack with `psql` inside the Compose PostgreSQL container; production restore procedures belong to the hosting runbook.

For a quick browser pass: sign in as `subscriber`, browse discovery and follow a creator; sign in as `luna-sparks` to inspect the creator profile; sign in as `admin` to inspect privileged routes. Use `make smoke` for the basic service check. See [production environment](PRODUCTION_ENVIRONMENT.md) for the work still required before any deployment.
