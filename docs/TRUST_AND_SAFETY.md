# Trust, Safety, Moderation and Compliance

Age/identity/performer consent, reporting, moderation, admin access, audit and security are foundational domains.

## Phase 15 communication boundary

Trust & Safety notices are transactional and contain only an action category, broad reason,
date, and appeal status/link where applicable. Notification payloads and delivery audit data
must not include evidence, reporter identity, moderator notes, identity documents, or raw
participant information.

# 30. Reporting, Moderation and Trust & Safety

Every user-visible content surface must expose a Report action where technically meaningful: profile, photo, video, gallery, post, comment, message, live, marketplace item and blog.

Phase 5 provides stable post/comment report targets and append-oriented report rows. A report is not a moderation decision; full cases and appeals remain the Trust & Safety expansion phase.


## 29.1 Moderation queue

- Priority/severity
- Reason taxonomy configurable by platform
- Evidence/context references
- Assign moderator
- Remove/restrict content
- Warn user
- Suspend/restrict account
- Escalate
- Request verification
- Dismiss report
- Appeal state where supported


## 29.2 Compliance foundations

- Creator identity/KYC
- Age verification
- Performer/co-performer identity and age verification
- Consent/releases attached to relevant content
- Prohibited-content policy enforcement
- Copyright/takedown process
- Geo restrictions where required
- Sanctions/payment compliance
- Record retention
- Audit logs
- Content/media hashes and provenance

Exact legal and payments requirements vary by jurisdiction and provider and must be validated before production launch. The architecture should make those obligations enforceable without rebuilding the content model.

## 29.3 Implemented compliance and age-assurance boundary

FanBackstage now has a provider-neutral, durable age-assurance lifecycle and a central reviewed jurisdiction resolver. It records normalized threshold, assurance, status, expiry/revocation, and exact policy provenance without retaining DOB, document images, provider access tokens, or whole result payloads. The VerifyMyAge browser adapter and deterministic local/test adapter implement the same interface and callback lifecycle. The earlier versioned 18+ self-attestation remains only a low-authority compatibility result; production rejects it as the selected provider.

Anonymous verification uses a durable server session referenced by a random HttpOnly, SameSite cookie whose hash is stored. It may attach to one account under row lock and cannot be reassigned. Provider verification never grants authentication or commercial entitlement.

Current KYC/billing/trusted-request/account country authorities must agree. A valid provider result retains its country as evidence provenance and is used as jurisdiction only when current authority is absent; it does not permanently veto a later authoritative country change. The existing age threshold/assurance is then tested against the newly applicable policy, so stronger requirements trigger re-verification while sufficient assurance is reused. Country registry, policy templates, country overrides, feature revisions, effective dates, review state, assurance, expiry, and re-verification are evaluated by one fail-closed resolver. Missing/conflicting current authority, provider outage, stronger requirements, disabled jurisdictions, or expired/revoked results deny protected operations. Restricted delivery URLs are capped to the remaining verification/session lifetime and reauthorised at each request.

Creator identity/KYC and creator adulthood remain separate from viewer age assurance. Performer identity, performer age, and per-performer consent releases are separate again. Every required linked performer must independently have current identity, age, and release authority. A creator cannot opt out of server-derived performer requirements, and one release cannot satisfy multiple performers.

When the effective creator-jurisdiction policy requires co-performer verification,
a legacy release-only content record is not verified performer authority. The
content must have explicit `VerifiedContentPerformer` links, and every link must
resolve current identity, age, and performer-specific release authority at
approval, public projection, and final delivery. The legacy scoped-release
fallback remains available only when that policy does not require verified
co-performers.

The repository does not contain reviewed real country law, production legal text, a retention duration, or real creator-KYC/payment providers. Demo PT/GB/US policies and legal bodies are fictional test data rejected by production readiness. See `COMPLIANCE.md`, `AGE_VERIFICATION.md`, `JURISDICTION_POLICY.md`, and `LEGAL_CMS.md`.

# 31. Admin Backoffice

- Global overview dashboard
- User and creator search
- Profile inspection
- Verification/KYC workflow integration
- Content review
- Report queue
- Live moderation/escalation tools
- Financial and payout views
- Commission configuration
- Promotion/featuring inventory configuration
- Referral settings
- Group/contract visibility
- Support actions
- Feature flags
- Taxonomy/categories/tags
- Audit log viewer


## 30.1 Admin impersonation

Use explicit temporary impersonation sessions rather than sharing credentials or silently assuming the user identity. Display a persistent banner, log actor/admin, target user, reason, start/end time and all sensitive actions. High-risk operations can remain blocked during impersonation.

# 32. Audit Logging

- Manager changed creator price or settings.
- Admin removed/restricted content.
- Creator joined/left group.
- Contract proposed/accepted/ended.
- Payout destination changed.
- Payout requested/approved/failed.
- Admin impersonation started/ended.
- Permission granted/revoked.
- Commission rule changed.
- Promotion created/edited/cancelled.

Audit logs should be append-oriented, timestamped, actor-aware and resistant to ordinary application-level deletion.

Compliance audit includes template/country/feature revisions, verification starts/results/reviews/revocations/attachments, provider probes, performer changes, consent changes, normalized creator-verification changes, legal draft/publish/retire/acceptance, and site-settings versions. Public clients receive safe reason/action codes, never moderator notes, provider credentials, raw evidence, or private performer identity.

# 39. Security Requirements

- Server-side authorisation on every protected operation.
- MFA support, especially creators/managers/admins.
- Strong session management and revocation.
- Rate limiting and abuse detection.
- CSRF protection where relevant; secure cookie strategy.
- Password hashing using modern algorithm.
- Secrets outside source control.
- Signed webhook validation.
- Encryption in transit and appropriate encryption at rest.
- PII and KYC segregation/minimisation.
- Admin least privilege.
- Sensitive actions may require re-authentication.
- Backup and restore testing.

## Phase 11 discovery safety

Discovery serves only approved public creators and currently eligible public objects. It excludes suspended/removed/moderated source state before ranking and relies on the existing safe preview resolver for locked media. Responses never contain original-media URLs, storage keys, private-message attachments, KYC/location fields, or entitlement internals. Public search is rate-limited to reduce enumeration and all dynamic filters are validated server-side.
