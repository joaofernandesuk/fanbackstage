# Age verification

This document describes the provider-neutral age-assurance implementation. It is not identity/KYC, a date-of-birth store, legal advice, or a claim that a provider outcome is sufficient in a particular jurisdiction.

## Provider boundary

`app.integrations.age_verification.base.AgeVerificationProvider` defines four operations/contracts:

- `get_capabilities()`;
- `create_verification_session(ProviderStartRequest)`;
- `exchange_browser_callback(code)`;
- `get_provider_status(callback_url)`.

Providers return normalized `ProviderVerificationResult` values only: provider verification ID, lifecycle status, age-verified result, achieved assurance, achieved minimum-age threshold, optional verified/expiry times, safe failure code, and retryability. Provider errors are normalized into stable configuration/unavailable/error classes. Product routes and services never import provider-specific response shapes.

Durable provider adapters are:

- `test`: deterministic browser provider for automated/local testing; blocked outside test unless explicitly enabled in development and always blocked in production;
- `verifymyage`: the production-capable browser adapter.

`development_self_attestation` remains a configured legacy compatibility authority, not a durable provider adapter. The registry reports it as non-durable/blocked for provider starts, and production rejects it.

## VerifyMyAge adapter

The adapter is `app.integrations.age_verification.verifymyage.VerifyMyAgeProvider`. It currently implements the browser OAuth-style flow verified against the selected integration contract:

- production base: `https://oauth.verifymyage.com`;
- sandbox base: `https://sandbox.verifymyage.com`;
- authorization: `GET /oauth/authorize` with `client_id`, `scope=adult`, lower-case country, callback URI, opaque state, and optional internal user reference;
- code exchange: `POST /oauth/token` with JSON `{ "code": ... }` and HTTP Basic client credentials;
- normalized result: `GET /users/me` with the transient access token; the documented `id` is the unique verification reference, not a user profile identifier;
- diagnostics: `GET /v1/business/allowed-redirects`, identifying with the API key/client ID and signing the request URI with the API secret.

The adapter advertises browser callback support, no webhook callback, no provider-side revocation capability, and conservatively normalizes a success as `low` assurance. The documented legacy result proves a fixed age-18 threshold but does not identify which upstream method produced it, so FanBackstage does not infer `medium` or `high`. Production readiness rejects a VerifyMyAge policy that demands a stronger assurance tier or a minimum age above 18. Such a jurisdiction needs a method-bound v2/v3 adapter (or another provider adapter) that can prove the stronger normalized outcome. A webhook is not fabricated. Future providers can implement the protocol without changing product routes.

Environment variables are:

```text
FANBACKSTAGE_AGE_ASSURANCE_PROVIDER
FANBACKSTAGE_AGE_TEST_PROVIDER_ENABLED
FANBACKSTAGE_AGE_PROVIDER_TIMEOUT_SECONDS
FANBACKSTAGE_AGE_PROVIDER_PROBE_MAX_AGE_SECONDS
FANBACKSTAGE_MANUAL_AGE_REVIEW_MAX_DAYS
FANBACKSTAGE_VERIFYMYAGE_ENVIRONMENT
FANBACKSTAGE_VERIFYMYAGE_CLIENT_ID
FANBACKSTAGE_VERIFYMYAGE_CLIENT_SECRET
FANBACKSTAGE_ANONYMOUS_COMPLIANCE_COOKIE_NAME
FANBACKSTAGE_ANONYMOUS_COMPLIANCE_SESSION_TTL_HOURS
```

Credentials remain server-side settings. They are not persisted in the compliance tables or exposed in admin APIs. Provider access tokens exist only during callback exchange and are not stored. The verified-result endpoint's contract places that transient token in a query parameter, so FanBackstage forces `httpx` and `httpcore` request logging to warning-or-higher in both API and worker processes; a regression asserts the token never reaches captured logs.

Production requires provider `verifymyage`, environment `production`, both provider credentials, an HTTPS API origin, a reviewed active policy selecting `verifymyage`, and a fresh healthy diagnostic matching the exact callback. Sandbox and test providers cannot satisfy production startup. The operator must confirm that its VerifyMyAge account supports this documented OAuth2 contract; VerifyMyAge's current SDK describes API v3 as the recommended contract, so an account that mandates v3 requires a new adapter implementation rather than silently changing this adapter's callback semantics.

VerifyMyAge country availability is also account/contract specific. Public legacy documentation lists only a subset and directs operators to the provider for additional countries. Before activating a production jurisdiction, obtain provider confirmation that the exact production account and selected contract support that country. An unsupported country fails closed at provider start; FanBackstage does not invent a global country list or downgrade the jurisdiction policy.

### VerifyMyAge sandbox in staging

For an isolated staging environment, set `FANBACKSTAGE_ENVIRONMENT=staging`, `FANBACKSTAGE_AGE_ASSURANCE_PROVIDER=verifymyage`, and `FANBACKSTAGE_VERIFYMYAGE_ENVIRONMENT=sandbox`; supply the sandbox-only API key/client ID and API secret through the staging secret store. Configure an HTTPS staging API origin and register the exact `/api/v1/compliance/age-verification/callback/verifymyage` URL with the sandbox account. Keep `FANBACKSTAGE_AGE_TEST_PROVIDER_ENABLED=false`, publish explicitly reviewed non-demo staging policies that select `verifymyage`, and confirm the allowed-redirect diagnostic before testing. Never reuse production credentials or provider data in staging. A production process rejects `sandbox` even if every other value is present.

## Browser flow and API

1. The client loads `GET /api/v1/compliance/countries` and uses an enabled ISO country.
2. It can inspect `GET /api/v1/compliance/decision` for a feature/restriction decision.
3. It sends `POST /api/v1/compliance/age-verification/start` with a country and safe internal return path.
4. The server resolves the effective reviewed policy, creates a durable pending record, creates/reuses an anonymous session when logged out, and asks the selected provider for an authorization URL.
5. The client accepts only HTTPS provider URLs, plus HTTP loopback URLs for local testing, then redirects.
6. The provider returns to `GET /api/v1/compliance/age-verification/callback/{provider_name}` with opaque `state` and `code`.
7. The server row-locks the record, validates the provider/state, serializes duplicate delivery, consumes state once, exchanges the code, stores the normalized outcome, commits audit/notification evidence, and returns with HTTP 303 to the prevalidated internal path.
8. `GET /api/v1/compliance/age-verification/status` returns the safe current fan summary and a separate creator-verification summary.

The callback state is random and persisted only as SHA-256. It is one-time and bounded by the configured anonymous-compliance session TTL even for authenticated starts; an anonymous start also requires its durable session to remain current and unrevoked. Callback event identity is hashed, unique per provider, and replay-safe. Return paths must be same-origin root-relative paths. Provider failures are normalized; they never grant access.

The callback query carries transient OAuth `state` and authorization `code` values. Repository API launchers disable Uvicorn's raw access log and retain only the application's query-free structured completion log. Callback responses, including normalized errors, use `Cache-Control: no-store` and `Referrer-Policy: no-referrer`. Production ingress, load-balancer, CDN, WAF, and APM configuration must redact the entire query string for `/api/v1/compliance/age-verification/callback/*`; raw callback URLs must never enter logs, traces, analytics, or error reports.

## Durable state

`AgeVerificationRecord` stores:

- authenticated user or anonymous compliance-session subject;
- provider and provider verification reference;
- one-time state hash/consumption time;
- safe return path;
- country and exact applicable jurisdiction-policy ID/version;
- required minimum age and assurance;
- achieved threshold and assurance;
- `pending`, `verified`, `failed`, `expired`, `revoked`, or `review_required`;
- initiation, verification, failure, expiry, and revocation times;
- safe reason code, retryability, and minimal normalized metadata (`age_verified` and threshold).

It does not store DOB, document images, address, provider access token, or whole provider payload. `AgeProviderCallbackEvent` provides replay evidence; `AgeProviderProbe` records diagnostic state/capabilities without credentials.

The effective age decision uses the strongest current valid record that meets the current policy. Upstream expiry and anonymous-session expiry are hard bounds. Policy `reverify_after_days` may shorten validity; configured grace can extend only that platform interval and never an upstream expiry or failed/revoked record. A stronger country/policy requirement returns `AGE_ASSURANCE_INSUFFICIENT` and requires a new verification.

Revocation is an immutable tombstone. The latest `revoked_at` is the authority cutoff: pending/failed attempts and results verified at or before that time cannot restore access or jurisdiction evidence, and a revoked row cannot be re-approved in place. Only a new successful verification strictly after the cutoff restores authority. A scheduled worker marks provider-expired records expired and sends safe action notices. Admin manual review cannot create an indefinite approval.

## Anonymous verification and account linking

Logged-out verification creates `AnonymousComplianceSession` with a random HttpOnly, SameSite=Lax cookie secret; only its hash is stored. The session has a bounded expiry and can be revoked. Verification records link to the durable session, not merely to a client flag.

At signup or later authenticated attachment, `attach_anonymous_session` locks the session and records, permits idempotent attachment to the same user, rejects cross-user reuse/reassignment, copies the account subject onto the records, audits the attachment, and clears the anonymous cookie. If the effective `new_fan_registration` policy requires fan verification, registration first requires a satisfying anonymous record and attaches it in the same registration transaction. Verification itself never creates a login session or entitlement.

## Legacy self-attestation

The earlier 18+ acknowledgement remains a compatibility assurance of `self_attested`, not provider verification. A reviewed policy may accept it only when its configured minimum is at most 18 and required assurance is no stronger. Production cannot select `development_self_attestation`. The new durable provider flow should be used whenever the effective policy requires provider-backed assurance.

## Provider outage behavior

Provider timeout, invalid response, missing credentials, invalid callback, stale probe, or unresolved policy fails closed. Existing still-current verified records remain governed by their stored expiry and current policy; the system does not disable age requirements because a new verification cannot start. Retryable failures are labelled for retry without exposing upstream details. Protected media, purchases, Live, messaging, and other gated commands stay denied when a required assurance cannot be established.

## Local test provider

Automated E2E uses `FANBACKSTAGE_ENVIRONMENT=test`, provider `test`, and a reviewed demo PT policy. The adapter exercises the same pending record, callback, normalization, attachment, audit, and resolver lifecycle; it is not a route-level bypass. For manual development testing, enable `FANBACKSTAGE_AGE_TEST_PROVIDER_ENABLED=true` deliberately and select provider `test`. Never enable it in staging/production or treat demo outcomes as evidence.
