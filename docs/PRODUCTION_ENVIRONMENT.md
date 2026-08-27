# Production environment requirements

No production environment is configured by this repository. Production startup rejects
development or unknown age-assurance/KYC providers, every unimplemented payment provider, demo
seeding, default session/notification/LiveKit/storage secrets, insecure cookies, local
Mailpit, and local MinIO. The only implemented identity integrations are development
self-attestation and development creator KYC, so changing an environment variable to an
arbitrary provider name cannot make a production deployment pass readiness validation.
Only the development payment adapter exists today, so production is intentionally blocked
until a real provider command and signed, replay-safe callback adapter are implemented and
registered. Failed PPV and initial-subscription charges retain immutable command snapshots
and durable ordered attempt history; a new idempotency key retries the canonical command,
while stale failures cannot fail a later attempt. The first verified PPV success across the
attempt history settles the canonical purchase. A second provider capture cannot grant or post
value twice: it creates a frozen `payment_refund_requirements` record in `required` state and an
audit event. A production payment adapter must implement the provider refund command and signed,
replay-safe completion callback that resolves this requirement; operations must treat every
`required` row as an unresolved customer liability until then.

Provision unique secrets for PostgreSQL, Redis, object storage, sessions, payment webhooks, notification webhooks, LiveKit, and any encryption keys. Use HTTPS origins, secure cookies, a transactional provider with SPF/DKIM/DMARC and bounce handling, separate production payment credentials, private object storage with CDN/signed-URL policy, and managed Redis/PostgreSQL credentials. When API storage I/O uses a private network hostname, configure `FANBACKSTAGE_STORAGE_PUBLIC_ENDPOINT_URL` with the browser-reachable HTTPS signing host; it changes only presigned upload/download hosts and never makes the bucket public.

For `FanBackstage.com`, decide canonical web host, API host, media/CDN host, transactional and marketing mail subdomains before DNS/TLS work. Do not point DNS until the deployment checklist is approved.

Production requires real age-assurance and creator-KYC adapters with authenticated,
signed, replay-safe callbacks and an explicit jurisdiction/provider policy. The local
18+ acknowledgement is a baseline self-attestation only and is not a substitute for that
integration. `FANBACKSTAGE_DEVELOPMENT_KYC_HTTP_ENABLED` is default-off and may be enabled
only with the development provider in an explicit development or isolated test process;
staging and production always reject it.
