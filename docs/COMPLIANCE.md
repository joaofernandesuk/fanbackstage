# Compliance architecture and operations

This document describes technical controls in FanBackstage. It is not legal advice, does not populate real-world country rules, and does not claim that a configured policy makes a deployment compliant. Every production jurisdiction and legal text must be supplied and approved by qualified operators and counsel.

## LiveGemini reference

The earlier read-only LiveGemini audit was used only as a conceptual reference. FanBackstage reuses the useful shape of a central country resolver, persistent attempt records, anonymous verification with later account attachment, safe return paths, final protected-media enforcement, structured denial, and provider diagnostics. It deliberately replaces LiveGemini's hardwired provider calls, wholesale response storage, long-lived unversioned state, single country boolean, and missing expiry/revocation/assurance/review/audit controls with the provider-neutral and versioned contracts below. No LiveGemini source or production state is modified or shared.

## Domain boundaries

The compliance domain resolves whether a feature is available for a trusted jurisdiction and whether the current viewer has enough age assurance. It deliberately does not turn one result into another authority:

- fan age assurance does not authenticate a user or grant a purchase/subscription entitlement;
- creator identity/KYC and creator adulthood remain creator-domain records;
- payout eligibility is not inferred from viewer age or creator profile visibility;
- performer identity, performer age, and consent/releases are private, separately evaluated records;
- legal-document acceptance points to exact immutable legal versions;
- media delivery reauthorises compliance, moderation, ownership, surface eligibility, and entitlement at the final redirect boundary.

The central resolver is `app.compliance.policy.resolve_compliance_decision`. HTTP routes use `app.compliance.http.resolve_request_compliance_decision`, which is the only adapter allowed to consume the configured trusted-country header and compliance cookie. Domain services and workers call the same resolver with explicit trusted signals.

## Decision contract

Every decision contains:

- `allowed`, a public stable `code`, an optional `action`, and a safe `reason`;
- the feature and resolved jurisdiction;
- policy ID/version internally and policy version publicly;
- configured minimum age and required assurance;
- achieved assurance and age-access result;
- independent feature availability;
- country-conflict state and verification expiry.

Representative fail-closed codes are `JURISDICTION_UNRESOLVED`, `COUNTRY_SIGNAL_CONFLICT`, `JURISDICTION_BLOCKED`, `POLICY_UNAVAILABLE`, `FEATURE_UNAVAILABLE`, `ANONYMOUS_ADULT_PREVIEW_UNAVAILABLE`, `AGE_VERIFICATION_REQUIRED`, `AGE_VERIFICATION_EXPIRED`, `AGE_VERIFICATION_REVOKED`, and `AGE_ASSURANCE_INSUFFICIENT`. Public actions are limited to safe next steps such as `LOGIN`, `VERIFY_AGE`, `RETRY_LATER`, or `CONTACT_SUPPORT`.

Missing, invalid, conflicting, disabled, unreviewed, expired, or provider-unavailable authority denies the protected operation. It never falls back to entitlement, admin role, frontend visibility, or a provider timeout. Structured denial is returned to product surfaces while direct protected media continues to use a non-enumerating denial.

## Surfaces using the resolver

The resolver is composed into content/media, discovery, feed, Stories, messaging, Live, Marketplace, PPV, subscriptions, Featuring, marketing-email delivery, and registration. New paid mutations require both their domain feature and the umbrella `purchases` feature. A denied command creates no payment attempt, reservation, participant, session, or entitlement. Marketing intents still require ordinary consent/preferences and suppression checks; the jurisdiction decision is an additional delivery-time condition, while transactional notices are unaffected.

Live start/join/token/chat/history and participant creation are gated before mutation. Feed and Story responses derive restriction from their actual media and referenced content; final media delivery rechecks the decision. Message attachment delivery remains blocked when age assurance expires even if the message was purchased. Marketplace listing media remains `safe_public` and purpose-bound; feature/country rules independently govern browse and purchase.

## Separate creator and performer controls

Creator verification stores normalized identity and adult outcomes separately on `CreatorVerification`. Public/publishing eligibility evaluates the latest current record; fan verification is never accepted as creator KYC. The current repository still has only a development creator-KYC provider, so production startup remains blocked until a real provider adapter and callback are implemented.

Private performer records are `PerformerIdentity`, `PerformerIdentityVerification`, `PerformerAgeVerification`, and `VerifiedContentPerformer`. Content with linked performers must satisfy every required performer link independently. A consent release for one performer cannot satisfy another performer. Revoked, expired, failed, incomplete, or missing identity/age/release authority fails closed. These private records are not projected as public profile identity.

The effective creator-jurisdiction policy controls whether the strict
co-performer path applies. When `co_performer_verification_required` is true,
release-only legacy content cannot pass verified-consent checks: explicit
`VerifiedContentPerformer` links and current identity, age, and scoped release
authority are required for every linked performer. Approval, public projection,
and final media delivery call the same authority resolver. The historical
release-only fallback is retained only for policies where that strict rule is
false.

## Backoffice

Open `http://localhost:13000/admin/compliance` in the local stack. The workspace contains:

- Overview;
- Countries / Jurisdictions;
- Policy Templates;
- Feature Flags;
- Age Verification attempts and review queue;
- Providers & Diagnostics;
- Policy Simulator;
- links to creator KYC, performers, consent, and legal controls;
- Compliance Audit.

The simulator calls the real resolver with a selected feature, country, optional existing user, and restricted-media flag. It does not maintain a second rules engine and does not mutate policy or verification state.

Secrets are environment configuration and are never returned by provider inventory, diagnostics, attempts, audit, or the simulator. Attempts expose only normalized status, provider reference scope, country, applicable policy/version, required/achieved threshold and assurance, lifecycle times, safe failure code, and retryability. Raw provider response payloads and access tokens are not stored.

## Permissions

- `compliance.view`: policy, provider, simulator, and audit views.
- `compliance.policy.manage`: template revisions.
- `compliance.jurisdiction.manage`: country registry and jurisdiction revisions.
- `compliance.verification.view`: normalized attempt search.
- `compliance.verification.review`: bounded manual review.
- `compliance.provider.manage`: provider probe command.
- `feature_flag.manage`: append-only global/country feature revisions.
- `legal.document.edit`, `legal.document.publish`, and `site_settings.manage`: separate legal/site controls.

Moderators have compliance view and verification-review scopes. Ordinary admins can view/review attempts and edit legal drafts/site settings. Only super admins receive every capability, including policy, jurisdiction, provider, feature-flag, and legal-publish powers. Backend authorization is authoritative; navigation visibility is not.

Manual verification approval requires a reason, the exact current policy threshold and assurance, and a finite expiry bounded by both policy re-verification and `FANBACKSTAGE_MANUAL_AGE_REVIEW_MAX_DAYS`. Review, policy, provider, performer, consent, creator-verification, legal, and site-settings changes emit audit events. Request-origin IP and a bounded user-agent string are attached inside the server request context where available but are not exposed to ordinary users; worker/system events retain null request metadata. PostgreSQL rejects substantive audit updates and every audit deletion, while preserving the existing actor-null privacy-erasure path without changing the event's action, object, reason, time, or metadata.

## Notifications and scheduled reconciliation

Provider completion/action-required, manual review, revocation, expiry, and impending expiry create safe transactional notification intents without provider evidence or identity details. Celery runs `reconcile_age_verifications` every five minutes to expire due records and enqueue bounded upcoming-expiry notices. Immediately effective mandatory legal publications start a bounded, deduplicated `LEGAL_ACCEPTANCE_REQUIRED` fanout to accounts for which that exact version applies; a five-minute scheduler continues the batches and begins future-dated versions only at their effective time. It never auto-accepts a document.

## Production readiness

`/ready` composes database-backed compliance and legal readiness in production. Production is denied when an active demo template/policy/feature exists, there is no reviewed effective non-demo jurisdiction policy, a policy selects a provider different from the configured production provider, the latest provider probe is missing/stale/unhealthy/misconfigured/wrong-callback, required reviewed legal baselines are absent, or an active published legal version is marked demo.

Before any public launch an operator must:

1. obtain real legal review for every enabled jurisdiction and legal document;
2. create reviewed non-demo templates, country revisions, feature flags, and effective dates;
3. configure a VerifyMyAge production account and HTTPS callback, then run a successful provider probe;
4. configure trusted GeoIP input or an explicitly reviewed fallback country and test conflict behavior;
5. publish approved non-demo legal documents and test exact mandatory acceptance;
6. decide and configure evidence/audit/legal retention outside this repository—no duration is invented here;
7. test blocked, stronger-assurance, expiry, revocation, country-change, and provider-outage cases;
8. implement and configure real creator-KYC and payment providers, which remain separate production blockers;
9. complete security/privacy review, backup/restore, monitoring, alerting, and incident runbooks.

Do not deploy publicly, configure live provider secrets in source, infer country law from the demo policies, or describe this technical implementation as legal compliance.

## Related documents

- [Age verification](AGE_VERIFICATION.md)
- [Jurisdiction policy](JURISDICTION_POLICY.md)
- [Legal CMS](LEGAL_CMS.md)
- [Trust and Safety](TRUST_AND_SAFETY.md)
- [Production environment](PRODUCTION_ENVIRONMENT.md)
