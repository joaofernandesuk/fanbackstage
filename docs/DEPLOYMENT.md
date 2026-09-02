# Deployment runbook

Deploy an immutable SHA-tagged build through a private staging environment first. The shared backend image serves API, worker, beat and one-shot migration roles and includes FFmpeg/FFprobe; the web image runs the Next standalone server. Never run development Dockerfiles, auto-reload or shared demo credentials in a shared environment. See [private staging deployment](STAGING_DEPLOYMENT.md).

Run migrations exactly once as a controlled release step, then start API and Celery workers with separate worker capacity and exactly one beat scheduler. Confirm `/health`, readiness reasons, worker/beat freshness, safe queue-depth events, database connectivity, private bucket access, signed provider callbacks and private-sink email delivery before routing traffic. Liveness never substitutes for readiness.

Roll back all application images to a mutually compatible prior release. Do not automatically downgrade data migrations: every migration must be reviewed for lock duration and reversibility, and released migrations are immutable. Prefer corrective forward migrations. PostgreSQL needs encrypted automated backups, point-in-time recovery where supported, documented retention, and scheduled restore drills into a separate target. Redis is for queues/rate limits/cache only, never durable business truth.

`20260831_0046_live_commerce` is explicitly forward-only: it establishes
payment-backed Live commerce charges and references immutable ledger/audit
history. Release and CI validation must verify a clean upgrade to head and must
expect its downgrade to refuse; an application rollback is allowed only when it
remains compatible with the migrated schema. Correct operational or data issues
with a reviewed forward migration, never a destructive schema rollback.

Media workers need FFmpeg installed, bounded concurrency/timeouts, retry monitoring, and cleanup of temporary objects. LiveKit production needs TLS, TURN/STUN, bandwidth and regional capacity planning, and an explicit recording policy.
