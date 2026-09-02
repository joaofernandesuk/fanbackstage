# Private staging deployment

This repository can describe and build a private staging release; it does not provision or deploy one. Staging is a shared environment and must never be assembled from `docker-compose.dev.yml`, development providers, shared demo passwords, or a publicly reachable host.

## Required external services

- managed or otherwise externally operated PostgreSQL with explicit asyncpg TLS;
- authenticated TLS Redis for queues, rate limits, and ephemeral operational state;
- a private, versioned, encrypted S3-compatible bucket with narrowly scoped workload credentials;
- an authenticated private SMTP sink that cannot deliver to the public Internet;
- remote LiveKit over WSS, with TURN/TLS capacity and its signed webhook registered at the staging API;
- a provider-neutral identity-aware access gateway with MFA;
- a trusted edge country signal that strips browser input before injection;
- error tracking/log aggregation configured with PII disabled.

The fictional payment and creator-KYC sandbox adapters are staging-only integration boundaries. They exercise signed asynchronous events without claiming production capability. VerifyMyAge remains red until a sandbox account, credentials, callback and provider-console confirmation exist. No placeholder is production capability.

## Build and configuration

Build `apps/api/Dockerfile` once for API, Celery worker, beat and migrations. Build `apps/web/Dockerfile` with the exact staging web/API origins. Tag both with the immutable release SHA and record their digests. Copy `.env.staging.example` to the secret/configuration system, never into the repository. The API rejects localhost origins, weak secrets, insecure cookies, public indexing, development providers and default local infrastructure.

`docker-compose.staging.yml` is a topology reference, not an infrastructure provisioner. Attach its web/API services to the private access-gateway network; do not publish application ports directly. The internal application network carries web/API traffic, while an unexposed egress network lets migrations, API, workers, and beat reach managed PostgreSQL, Redis, object storage, SMTP, LiveKit, and provider endpoints. Apply infrastructure-level egress policy to that network. The topology deliberately defines one beat service. Running multiple beat schedulers is unsupported unless a distributed scheduler lock is introduced.

## Access gateway and callbacks

Require MFA/IAP authentication for all normal web and API paths, including admin. API documentation is disabled. Permit only the minimum provider callbacks around the gateway: LiveKit, payment, notification and age-provider callbacks as applicable. Route exceptions remain protected by their application signatures/state/replay checks and bounded request bodies; the gateway must not rewrite, mock or bypass them.

The edge removes `FANBACKSTAGE_TRUSTED_COUNTRY_HEADER` from browser traffic, determines the country, then injects it only toward the API/Next services from one of the configured narrow peer CIDRs. It also supplies the country to Next so the signed Next-to-API handoff remains authoritative for anonymous legal/footer rendering. Uvicorn runs with `--no-proxy-headers` for this peer-trust model.

Staging returns `X-Robots-Tag: noindex, nofollow, noarchive`, `robots.txt` disallows `/`, and the sitemap is empty. These are defense in depth; private access remains mandatory.

## Controlled release order

1. Verify external backups, secret versions, service endpoints and access-gateway rules.
2. Build and scan SHA-tagged images.
3. Run one `migrate` job with `alembic upgrade head`; do not run Alembic from every API replica.
4. Start API and workers, then exactly one beat scheduler, then web.
5. Confirm `/health`; `/ready` is expected to remain unavailable while the explicit payment/KYC/VMA or policy/legal blockers remain.
6. Inspect structured request/error logs, worker/beat heartbeat and queue-depth events.
7. Run signed callback, media upload/delivery, legal acceptance, notification-sink and live-control smoke tests.

The API emits query-free request latency/status/correlation fields and safe error event IDs. Celery emits a safe failure event for every queue and a LiveKit control-outbox batch metric; the operations heartbeat proves beat-to-worker delivery and reports named queue depths. Alert externally on API 5xx/latency, stale heartbeat, queue growth/age, task failures (including notification, media, finance reconciliation and signed-webhook work), and nonzero/repeated outbox retries. The repository provides these signals and readiness inputs, not a hosted metrics/error account.

If migration fails, stop the release and preserve logs. Do not automatically downgrade. Review migration semantics and prefer a corrective forward migration. Application rollback uses the prior web/API/worker image set only when it remains compatible with the current database head.

## Legal and compliance staging authority

Staging never auto-passes legal/compliance readiness. Publish only fictional/reviewed test policies and effective Terms, Privacy and Age Policy versions clearly carrying `STAGING TEST ONLY` and `is_demo=true`. Enable only countries with a complete effective policy. These records prove technical behavior and are not production legal approval.

## Administrator bootstrap

After SMTP/worker delivery works, run inside the API image:

```sh
python -m app.staging.bootstrap_admin --email nominated.operator@example.invalid --confirm BOOTSTRAP-STAGING-ADMIN
```

The command is staging-only, grants admin/super-admin idempotently, creates no known password, emits an audit event, and sends a short-lived one-time password-reset link. It never prints the token. There is no HTTP bootstrap endpoint.

## Fictional dataset and reset

Set `FANBACKSTAGE_STAGING_DATASET_ENABLED=true` only during an authorized dataset operation. Create the small fictional namespace with:

```sh
python -m app.staging.dataset create --credentials-file /operator-private/staging-credentials.json
```

The destination must not exist and is created mode `0600`; generated unique credentials are never printed. The dataset uses reserved `.invalid` identities, calls no providers, creates no fake financial history, and leaves creators non-public. Future financial fixtures must use domain services and actual sandbox-provider callbacks.

Reset only that namespace:

```sh
python -m app.staging.dataset reset --confirm RESET-STAGING-TEST-DATA
```

Foreign-key protection may refuse reset if operators attached durable non-test domain state; investigate rather than broadening deletion. Infrastructure and non-dataset accounts are never targets.

## Backup, restore and teardown

Use `scripts/staging-backup.sh` with an operator-only synchronous PostgreSQL URL. Store its restricted dump, version metadata and checksum manifest in encrypted, access-logged storage. The script rejects a `pg_dump` major version that differs from the source database. Enable object versioning and lifecycle retention; export configuration references and secret metadata separately without placing secret values in the backup log.

At least once before relying on staging, provision a new isolated database whose name contains `restore` or `validation`, then run `scripts/staging-restore-drill.sh`. The restore script checks the target database name and requires source server, `pg_dump`, target server and `pg_restore` to use the same PostgreSQL major version before running `--clean`. Apply current migrations, run readiness and smoke checks, record recovery time/checksum/release SHA, and destroy only the validation target. Never test restore by overwriting staging. PostgreSQL point-in-time recovery and object retention are external provider responsibilities.

Teardown removes the access routes and application workloads first, then snapshots/retains data according to the approved policy before removing external services. Never reuse staging credentials elsewhere.

## Operator-only gaps

The full moderator/admin browser backoffice is outside this block. Some report, appeal, consent, creator review, group and referral operations still require permissioned API/manual operator use. Record every such runbook action and do not describe those workflows as complete UI acceptance.
