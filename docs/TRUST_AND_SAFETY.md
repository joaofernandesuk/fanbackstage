# Trust, Safety, Moderation and Compliance

Age/identity/performer consent, reporting, moderation, admin access, audit and security are foundational domains.

# 30. Reporting, Moderation and Trust & Safety

Every user-visible content surface must expose a Report action where technically meaningful: profile, photo, video, gallery, post, comment, message, live, marketplace item and blog.


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
