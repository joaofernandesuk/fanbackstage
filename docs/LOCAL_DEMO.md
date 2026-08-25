# Local demo

This data is fictional, harmless, and development-only. From `codex/production-readiness`:

```sh
make dev
make demo-seed
make dev-status
```

Open `http://localhost:3000`, API documentation at `http://localhost:8000/docs`, Mailpit at `http://localhost:8025`, and MinIO at `http://localhost:9001`. LiveKit listens on `ws://localhost:7880`.

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

Use `make dev-backup` to create a disposable local PostgreSQL dump under `.fanbackstage-dev/backups/`. A restore drill should be performed only against a reset local stack with `psql` inside the Compose PostgreSQL container; production restore procedures belong to the hosting runbook.

For a quick browser pass: sign in as `subscriber`, browse discovery and follow a creator; sign in as `luna-sparks` to inspect the creator profile; sign in as `admin` to inspect privileged routes. Use `make smoke` for the basic service check. See [production environment](PRODUCTION_ENVIRONMENT.md) for the work still required before any deployment.
