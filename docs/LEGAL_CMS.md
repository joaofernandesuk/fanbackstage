# Legal CMS and version acceptance

This document describes technical controls. It is not legal advice and does not claim that any document text or jurisdiction policy is legally sufficient. Production text must be supplied and approved through the platform's real legal-review process.

## Domain boundary

The legal CMS owns published legal text, its lifecycle, its audience/country/language scope, exact user acceptance, and small public site settings. It does not own age-assurance decisions, identity/KYC, cookie-consent state, entitlements, billing-country evidence, or GeoIP. Those signals remain with their existing domain owners.

`LegalDocument` is the immutable stable scope: type, slug, optional country, language, and audience. A changed scope is a new document rather than a rewrite of historical acceptance meaning. `LegalDocumentVersion` stores the body and publication lifecycle. A draft is editable. A published or retired body is immutable; a content change creates the next numbered draft. PostgreSQL triggers enforce document-scope, published-version, acceptance-evidence, and site-settings-history immutability independently of the service layer. Publication requires:

- a draft version;
- no outstanding legal-review flag;
- explicit approval for publication;
- a valid effective window;
- a privileged, confirmed action with a reason;
- non-demo text in production.

Retirement changes lifecycle metadata only. It never changes the retained body. `LegalAcceptance` points to one exact immutable version, so a later version cannot rewrite historical meaning.

## Safe content

Legal bodies are stored as validated structured blocks: headings, paragraphs, ordered or unordered lists, callouts, and links. Admins may edit the same subset using plain Markdown. The frontend renders text through React elements and never uses `dangerouslySetInnerHTML`. External links must be HTTPS and cannot contain URL credentials; internal links must be root-relative.

## Resolver

The resolver chooses an effective, approved, published version in this order:

1. exact trusted jurisdiction, then global fallback;
2. requested language, then English fallback;
3. applicable account audience, then `all_users`;
4. highest effective version.

Authenticated mandatory acceptance never trusts a country in the request body or query. Routes call the shared compliance HTTP resolver, which considers the authenticated account and configured trusted request signals and rejects conflicts. A country selector on a public legal browsing route is only an additional signal: it cannot override a conflicting authenticated/trusted signal.

Affiliate scope uses the canonical active `AffiliatePartner.owner_user_id` relationship. There is no synthetic affiliate role.

## Acceptance

Public authenticated acceptance accepts only:

```json
{
  "version_ids": ["uuid"],
  "source": "interstitial"
}
```

The ordinary source is `interstitial` or `account`; clients cannot claim `registration`. The service verifies that every submitted version is currently effective, requires acceptance, matches the account audience, and is the most specific applicable version. Replay returns the existing acceptance instead of creating a duplicate.

Registration uses `prospective_registration_requirements`, `validate_registration_acceptances`, and `record_registration_acceptances`. Auth must resolve a trusted country before account creation, validate an exact no-more/no-less set of current fan plus all-user versions, create the user, and record acceptances in the same transaction. The UI must leave every checkbox initially unchecked.

## Permissions and audit

- `legal.document.edit` lists and edits drafts and creates new versions.
- `legal.document.publish` publishes or retires a version.
- `site_settings.manage` creates a new site-settings version.

Draft creation/update, publication, retirement, acceptance, and site-settings updates emit scrubbed audit events. Publication and retirement require explicit confirmation and a reason.

## Public site settings

Site settings are a small append-only singleton, not a general page builder. Updates are serialized with a PostgreSQL transaction advisory lock. Each save marks the previous row non-current and inserts a new current version containing public contact/footer text, validated social links, and a time-bounded announcement or maintenance banner.

## Production readiness baseline

`production_legal_readiness` blocks production readiness when an effective demo legal version is published or when a reviewed, approved, effective, non-demo global English baseline is absent for:

- Terms;
- Privacy Policy;
- Age / Adult Content Policy.

Terms and Privacy are the registration baseline named in the compliance brief. The Age / Adult Content Policy is included because FanBackstage is adult-content-capable and the brief requires it at signup where applicable. This is a conservative technical launch requirement, not a statement about the content or law of any country. Cookie, creator, marketplace, performer, refund, and other documents remain independently configurable and may be made mandatory for their actual audience/policy.

Migration `20260827_0038` refuses downgrade while any legal or site-settings row exists. Operators must deliberately retain/export and resolve that history rather than silently deleting published text or acceptances.

## Routes and user experience

Public published documents resolve through `/api/v1/legal/documents` and `/api/v1/legal/documents/{slug}` and render at `/legal/{slug}`. The footer resolves its links and public contact/social copy from the CMS/site-settings APIs, so publishing a new body does not require a frontend deployment. The banner is a time-bounded site-settings value rendered as text; it cannot execute scripts.

`/api/v1/legal/me/requirements` returns the exact effective mandatory versions the current account has not accepted. The global `LegalAcceptanceGate` provides the interstitial, while the API independently returns structured `428 LEGAL_ACCEPTANCE_REQUIRED` for ordinary authenticated operations. Canonical request compliance decisions apply the same denial to final/public media paths. Legal documents, requirements, acceptance history, exact acceptance submission, logout, read-only account/session inspection and session revocation remain usable recovery paths; frontend visibility is never the authority. Signed-in marketing preference inspection and unsubscribe remain available so platform or legal denial cannot prevent consent withdrawal, while opt-in and ordinary product settings remain gated. `/account/legal` shows the account's version-level history without exposing request metadata. Registration fetches `/api/v1/legal/registration-requirements`, leaves every checkbox unchecked, submits exact version IDs, and records the accepted rows inside the account-creation transaction.

Admin routes live under `/api/v1/admin/legal` and `/api/v1/admin/site-settings`; the web workspaces are `/admin/legal`, `/admin/legal/{documentId}`, and `/admin/site-settings`. Granularly permissioned compliance/legal/site-settings routes bypass a disabled platform flag only so an authorised operator can recover policy, but still require the operator's own current legal acceptance. Publishing an immediately effective version that requires acceptance emits a bounded batch of deduplicated `LEGAL_ACCEPTANCE_REQUIRED` notification intents only for accounts for which that exact version is currently applicable and unaccepted. A five-minute replay-safe scheduler continues bounded fanout and activates future-dated publications only when they become effective; it neither notifies early nor marks acceptance automatically. Notifications link to `/account/legal`.

The local demo publishes explicitly marked placeholder Terms, Privacy, and Age Policy versions and leaves every other document type as a draft awaiting review. They exercise the resolver and acceptance lifecycle only. Production readiness rejects active demo legal versions and does not treat placeholder text as approval.

## Launch checklist

- Supply jurisdiction- and audience-appropriate text through qualified legal review.
- Clear every `requires_legal_review` flag only after real review and use the named publish permission.
- Publish reviewed, approved, non-demo, effective global English Terms, Privacy, and Age Policy baselines before production readiness can pass.
- Configure any additional creator, performer, Marketplace, refund, complaints, prohibited-content, copyright/takedown, or cookie documents actually required by reviewed policy.
- Test global and country-specific selection, language fallback, audience selection, successor versions, mandatory re-acceptance, notification targeting, retirement, and retained history.
- Decide retention and deletion/export procedures externally; the repository intentionally supplies no legal retention duration.
