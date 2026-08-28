# Production environment requirements

No production environment is configured by this repository. Production startup rejects
development or unknown age-assurance/KYC providers, every unimplemented payment provider, demo
seeding, default session/notification/LiveKit/storage secrets, insecure cookies, local
Mailpit, and local MinIO. Age assurance has a production-capable VerifyMyAge browser adapter,
but creator KYC remains development-only; changing an environment variable to an arbitrary
provider name cannot make a production deployment pass readiness validation.
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

Provision strong, non-placeholder secrets for PostgreSQL, authenticated Redis, object storage, sessions, payment webhooks, notification webhooks, LiveKit, VerifyMyAge, SMTP, and any encryption keys. Production validation rejects blank, short, known-default, or example credentials; default PostgreSQL host/credentials; unauthenticated or non-TLS Redis; non-WSS LiveKit; malformed/local API, web, storage, and LiveKit endpoints; and SMTP without authentication and transport encryption. `FANBACKSTAGE_DATABASE_URL` must use `postgresql+asyncpg` and select exactly one SQLAlchemy/asyncpg TLS value in the URL query: `ssl=require`, `ssl=verify-ca`, or `ssl=verify-full`. Missing TLS and the fallback-capable `ssl=disable`, `ssl=allow`, and `ssl=prefer` values are rejected. Use `ssl=`, not libpq's `sslmode=` query name, because SQLAlchemy passes the URL query directly as asyncpg connection keyword arguments. Prefer `ssl=verify-full` with the required trusted CA material; `ssl=require` encrypts the connection but does not verify the server certificate or hostname and needs an explicit reviewed provider constraint. Configure `FANBACKSTAGE_SMTP_USERNAME` and `FANBACKSTAGE_SMTP_PASSWORD`, then select exactly one transport mode: `FANBACKSTAGE_SMTP_USE_TLS=true` for implicit TLS (normally port 465) or `FANBACKSTAGE_SMTP_START_TLS=true` to require STARTTLS (normally port 587). Certificate validation remains enabled. Use secure cookies, a transactional provider with SPF/DKIM/DMARC and bounce handling, separate production payment credentials, private object storage with CDN/signed-URL policy, and managed Redis/PostgreSQL credentials. When API storage I/O uses a private network hostname, configure `FANBACKSTAGE_STORAGE_PUBLIC_ENDPOINT_URL` with the browser-reachable HTTPS signing host; it changes only presigned upload/download hosts and never makes the bucket public.

The API incrementally caps signed LiveKit webhook bodies at 64 KiB and notification-provider bodies at 16 KiB before parsing. The production ingress must enforce equal-or-smaller path-specific body limits and a conservative request-line/query limit (8 KiB recommended), reject chunked over-limit requests, and keep age-provider callback query credentials out of access/APM logs.

`FANBACKSTAGE_MEDIA_URL_TTL_SECONDS` is limited to 1–300 seconds. Protected-media redirects remain private/no-store and reauthorize before minting each short-lived URL; do not extend the signing TTL as a substitute for a private CDN authorization boundary.

For `FanBackstage.com`, decide canonical web host, API host, media/CDN host, transactional and marketing mail subdomains before DNS/TLS work. Do not point DNS until the deployment checklist is approved.

## Compliance, age assurance, and legal readiness

The provider-neutral age-assurance domain and VerifyMyAge browser adapter are implemented. Production startup requires all of the following:

- `FANBACKSTAGE_AGE_ASSURANCE_PROVIDER=verifymyage`;
- `FANBACKSTAGE_VERIFYMYAGE_ENVIRONMENT=production`;
- non-empty `FANBACKSTAGE_VERIFYMYAGE_CLIENT_ID` (the provider API key/client identifier) and `FANBACKSTAGE_VERIFYMYAGE_CLIENT_SECRET` (the provider API secret) supplied through the production secret store;
- HTTPS `FANBACKSTAGE_API_ORIGIN` and the exact registered callback at `/api/v1/compliance/age-verification/callback/verifymyage`;
- an intentionally reviewed `FANBACKSTAGE_COMPLIANCE_FALLBACK_COUNTRY`; a trusted country header plus explicit proxy CIDRs is optional higher-priority evidence and does not replace the fallback needed by SSR and server-to-server calls;
- completion of the explicit account-country migration/review queue: production readiness rejects any legacy authenticated account whose country is still null, and the fallback must never be bulk-written as factual account data;
- an API process configured not to rewrite the immediate peer from forwarded headers (`--no-proxy-headers` for Uvicorn); never use a wildcard forwarded-IP allowlist with the trusted-country-header model;
- when trusted GeoIP is enabled, paired `FANBACKSTAGE_TRUSTED_COUNTRY_HEADER` and canonical narrow `FANBACKSTAGE_TRUSTED_PROXY_CIDRS` values for the actual ingress peers; production rejects catch-all, broad, local, host-bit, duplicate, or oversized proxy networks;
- a strong shared `FANBACKSTAGE_INTERNAL_COUNTRY_HANDOFF_SECRET` in both the API and Next server environments. The edge must strip the configured country header from client requests, inject its own country result into the Next request, and prevent direct public access that bypasses that normalization. Next signs country, timestamp, and exact API path for anonymous legal/footer SSR; the API accepts only a fresh matching HMAC and never trusts a browser-supplied internal header;
- an explicit enabled-country allowlist, with a reviewed, effective, non-demo country/template rule for every enabled country and `verifymyage` selected as its provider; the seeded ISO catalogue is disabled by default and is not an availability promise;
- VerifyMyAge-backed rules no stronger than this legacy adapter's documented fixed-18, low-assurance normalized capability; stronger rules require a method-bound provider adapter before readiness can pass;
- written/provider-console confirmation that the selected VerifyMyAge account and OAuth contract support every enabled production country;
- no active demo feature revisions;
- a fresh healthy provider probe whose callback matches configuration;
- reviewed, approved, effective, non-demo global English Terms, Privacy, and Age Policy versions.

`/ready` checks the database-backed compliance and legal authority in production. It fails without those controls or when an active policy selects a provider different from the configured production adapter. The deterministic test adapter, development self-attestation provider, sandbox VerifyMyAge environment, demo policies, demo legal versions, and demo seed are all rejected in production.

No VerifyMyAge secret belongs in a database policy row or admin form. Policy stores only the adapter name and an optional non-secret provider policy reference. Access tokens are transient and whole provider payloads are not retained.

Repository API launchers disable Uvicorn's raw request access log because the age-provider callback uses transient query credentials. The API and worker also suppress `httpx`/`httpcore` request-line logging so VerifyMyAge's transient result token cannot be serialized from its contract-required query parameter. Keep the application's query-free structured request log, and configure every production ingress, CDN, WAF, APM, and error-reporting layer to drop or redact the query string for `/api/v1/compliance/age-verification/callback/*`. Callback URLs, OAuth state, authorization codes, and provider access tokens must never be retained.

The repository still has only a development creator-KYC adapter. `FANBACKSTAGE_DEVELOPMENT_KYC_HTTP_ENABLED` is default-off and may be enabled only with the development provider in an explicit development or isolated test process; staging and production reject it. Production therefore remains intentionally blocked until a real creator-KYC adapter and callback are implemented. Payment production readiness remains blocked independently because only the development payment adapter exists.

Before launch, operators must also obtain actual legal review, configure real jurisdiction rules rather than copying demo scenarios, decide provider/evidence/audit/legal retention, configure trusted GeoIP, verify provider redirects and outage behavior, publish applicable legal documents, test mandatory acceptance and stronger-policy re-verification, and complete security/privacy and incident-response review. See [compliance](COMPLIANCE.md), [age verification](AGE_VERIFICATION.md), [jurisdiction policy](JURISDICTION_POLICY.md), and [legal CMS](LEGAL_CMS.md).
