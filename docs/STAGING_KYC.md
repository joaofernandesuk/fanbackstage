# Staging creator-KYC sandbox

`staging_sandbox` is a fictional asynchronous creator-KYC adapter for `staging` and `test` only. It is intentionally separate from fan age assurance, performer verification and consent releases. No document image, selfie, date of birth or raw evidence is collected or stored.

## Flow and outcomes

An eligible pending creator application starts a provider-style KYC session with an opaque `stgkyc_` reference. The staging checkout action queues one fictional provider event. A worker signs and delivers it through the same callback parser exposed for an external provider. Outcomes are `VERIFIED`, `FAILED`, `REVIEW_REQUIRED`, and `EXPIRED`.

Callbacks require HMAC-SHA256, are body-bounded, and are deduplicated by `(provider, external_event_id)`. Unknown references and stale/conflicting terminal events cannot grant verification. A verified outcome moves only the creator application from pending verification to pending review; an authorised moderator still owns approval. Notifications contain a broad action/status only.

## Operations and safety

Authorised compliance operators can inspect a minimised creator-KYC projection at `GET /admin/compliance/creator-kyc`: provider, opaque reference, status, jurisdiction, timestamps, and safe failure category. Audit events cover start and normalised result changes.

The adapter is rejected in production even if configured. A future real KYC adapter must retain this lifecycle: start session, normalise a signed callback, deduplicate it, expose safe review/expiry state, and keep sensitive evidence outside FanBackstage's normal application database.

## Fictional staging personas

The guarded staging dataset creates creators with KYC not started, pending, verified,
failed, and review-required outcomes. Credentials are generated per creation run and
written only to the operator-selected mode-0600 credentials file; they are never
committed or reused as shared passwords.
