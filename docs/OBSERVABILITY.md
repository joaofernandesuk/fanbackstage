# Error tracking and operational diagnostics

FanBackstage uses its existing structured JSON logs as the primary operational record and a
provider-neutral error-tracking adapter for exception grouping and alerting. Sentry is the single
implemented external exporter because its Python, FastAPI/Celery, Next.js, browser, release, and
private source-map workflows cover the shared-runtime contract. The exporter does not own domain
state: database records, audit events, and the financial ledger remain authoritative.

## Configuration and privacy boundary

Local development and tests default to `FANBACKSTAGE_ERROR_TRACKING_PROVIDER=disabled`; this keeps
the network-free safe logging fallback. Staging and production require `sentry`, an HTTPS
`FANBACKSTAGE_ERROR_TRACKING_DSN`, `FANBACKSTAGE_ERROR_TRACKING_SEND_PII=false`, and an immutable
`FANBACKSTAGE_RELEASE_SHA`. Browser builds receive the matching `NEXT_PUBLIC_` environment,
provider, DSN, and release values. A browser Sentry DSN is a public ingestion identifier rather
than an authorization secret; use a separate browser project/DSN with provider-side origin and
rate restrictions. Server DSNs and all management/upload tokens remain secret-store values.

Both adapters drop request objects, users, breadcrumbs, arbitrary extras, exception messages,
stack locals, and all contexts except the controlled `fanbackstage` context. Therefore cookies,
authorization headers, callback query strings, request bodies, provider payloads, private messages,
payment/KYC evidence, and presigned URLs are outside the exporter boundary. Never weaken this
policy by enabling Sentry's default PII collection. Staging and production startup fail closed if
`FANBACKSTAGE_ERROR_TRACKING_SEND_PII` is true.

API events carry only the environment, release SHA, generated event ID, correlation ID, HTTP
method, normalized framework route, status, and a controlled category. Worker events add only the
registered task and routing queue names. Browser and Next runtime events carry environment,
release, normalized path without query/fragment, and runtime category. Expected handled failures
such as invalid login, permission denial, validation failure, or declined payment are not reported
as crashes. Browser reporting is bounded to five unhandled events per minute per page instance.

Alert categories are `api_uncaught_error`, `web_runtime_error`, `browser_unhandled_exception`,
`browser_unhandled_rejection`, `worker_task_failure`, `media_processing_failure`,
`payment_callback_failure`, `kyc_callback_failure`, and `livekit_control_failure`. Configure alert
rules and retention in the external Sentry projects; no account or alert rule is provisioned here.

## Staging diagnostic

An authenticated administrator can call
`POST /api/v1/admin/operations/error-tracking-diagnostic` in staging. The endpoint is unavailable
outside staging, emits one message containing no request or user data, returns the queued provider
event ID, and records `operations.error_tracking_diagnostic_requested` in the audit log with only
the provider and release SHA. Confirm the event arrives in the staging project and matches the
running image release. Do not expose this endpoint through an unauthenticated gateway exception.

## Releases and private source maps

Set both server and browser release variables to the Git commit SHA used to tag the API/web images.
The Sentry Next wrapper is prepared to associate that release with source maps. A later CI release
job may supply `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, and `SENTRY_PROJECT` to a dedicated web build.
The token needs only release/artifact upload scope and must be injected from CI secrets, never as a
Docker build argument or public environment variable. When all three values exist, the build
uploads maps and deletes them after upload; without them, upload is disabled. This repository does
not upload artifacts or provision Sentry in validation builds.

Provision externally: separate server/browser Sentry projects (or equivalently separated DSNs),
staging and production DSNs, origin/rate controls for the browser DSN, alert rules, retention and
access policy, and the least-privilege CI source-map token when release upload is approved.
