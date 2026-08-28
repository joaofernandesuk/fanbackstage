# Jurisdiction policy

The jurisdiction system is an operational configuration engine, not a database of laws. The repository intentionally seeds no real-world legal requirements. Production country rules must be supplied, reviewed, dated, and approved outside the software-development process.

## Trusted country resolution

The resolver accepts these separately owned current-jurisdiction signals:

1. creator/KYC country when explicitly supplied by that domain;
2. billing country when explicitly supplied by its domain;
3. request country from one configured header only when the immediate peer is in a configured trusted proxy CIDR;
4. authenticated account country.

The country snapshotted on a still-valid provider verification is historical evidence provenance, not an eternal current-location veto. It supplies jurisdiction only when no current authority exists. When a current authority resolves country B, the resolver evaluates the existing threshold and assurance against B's current policy: sufficient evidence is reused, while a stronger B policy returns re-verification required. A new provider flow started for B records B as the next provenance.

Signals are normalized against the built-in ISO 3166-1 alpha-2 identifier set. That set asserts codes only and contains no policy. Distinct current trusted countries do not silently override one another: they produce `COUNTRY_SIGNAL_CONFLICT` and deny the operation. Invalid/no signals use current valid provider provenance, then an explicit configured fallback. Development and test use PT only as a local fallback; production always requires a reviewed fallback, with a configured trusted header/proxy as optional higher-priority current evidence.

An account-entered country cannot overwrite conflicting trusted request, billing, or KYC authority. Conversely, an older provider-result country cannot trap an account in its historical policy after current authority changes. The compliance HTTP adapter is the only code that reads the trusted proxy header. Ordinary client headers, query parameters, or frontend state are not authoritative.

A creator profile's location country is display metadata and never selects creator policy. Creator eligibility resolves from the persisted account country plus current finite creator-KYC country evidence; disagreement fails closed. Development verification also snapshots that trusted account/fallback resolution, never the editable profile field.

FanBackstage's Uvicorn launchers disable proxy-header rewriting so `request.client` remains the immediate network peer used by that CIDR check. Production process managers must do the same (for Uvicorn, `--no-proxy-headers`) and must never combine this trust model with a wildcard forwarded-IP allowlist. The trusted reverse proxy may set the configured country header; ordinary clients may not.

Anonymous legal pages and footer links are rendered by Next rather than the browser. The trusted edge therefore supplies the visitor country to Next after stripping any client value; Next forwards it to the API only as a short-lived HMAC over country, timestamp, and exact API path. The API ignores missing, stale, forged, or path-replayed handoffs. The shared handoff secret is required in production and must not be exposed through a `NEXT_PUBLIC_` variable.

Production validates the trusted header and proxy list as a pair. The header must be a valid HTTP field-name token. CIDRs must be canonical, non-duplicate ingress peer networks; catch-all, unspecified, multicast, loopback, link-local, host-bit, IPv4 prefixes broader than `/16`, IPv6 prefixes broader than `/48`, and lists larger than 32 entries are rejected. Prefer the exact `/32` or `/128` peer addresses (or the narrowest provider-owned ingress subnet), firewall the API so clients cannot reach it around that ingress, and review every proxy-network change as jurisdiction-authority configuration.

`selected_country` is represented separately as an untrusted user choice. It may agree with or narrow trusted authority; disagreement creates a conflict. Production never elevates selection alone: it must agree with configured fallback or trusted evidence. Development/test deliberately permit selection alone so local provider and E2E flows can run without pretending the value came from GeoIP.

The operational fallback is for anonymous and request contexts that inherently lack an account authority. It is never written into, or silently applied as, the factual country of an authenticated legacy account. Production readiness remains blocked while any migrated account has a null country; those accounts require an explicit trusted country-establishment review rather than a bulk fallback backfill.

An authenticated account can establish/change country through the dedicated recovery command only when the selected country exactly matches current trusted-proxy GeoIP. Where trusted GeoIP is unavailable, a permissioned compliance operator can perform the same row-locked transition with explicit confirmation and reason. Both paths require an enabled reviewed effective destination policy and append an old/new/source audit event; ordinary profile/location fields never change this authority.

## Data model

- `CountryRegistry`: canonical code/name and an operational enabled flag.
- `CompliancePolicyTemplate`: stable reusable template identity.
- `CompliancePolicyTemplateRevision`: immutable versioned full rule set, status, effective window, review evidence, demo marker, and reason.
- `JurisdictionPolicyRevision`: immutable country version pointing at one template revision plus explicit overrides, effective window, review evidence, demo marker, and reason.
- `FeatureFlagRevision`: immutable global or country-scoped feature override with effective window, demo marker, and reason.

Statuses are `draft`, `scheduled`, `active`, and `retired`. Published authority requires an active/scheduled effective revision with reviewer evidence. Later revisions do not mutate earlier meaning. Version numbers are allocated under PostgreSQL advisory transaction locks. When multiple append-only successors are effective, the highest version is authoritative, making planned transitions deterministic while preserving history.

Migration `20260827_0037` creates the compliance, age, provider, and performer foundations. Its downgrade refuses while retained compliance evidence exists or normalized creator-KYC state would be lost.

## Template inheritance and overrides

`PolicyRules` is a complete, extra-fields-forbidden schema. A country revision inherits every field from its chosen template revision. Only fields explicitly present in `overrides_json` replace the inherited value; explicit `null` is preserved where the field permits null. This avoids copied country policies while keeping the effective result inspectable.

The rules include:

- platform, fan registration, creator registration, purchases, subscriptions, PPV, Live, Marketplace, Featuring, marketing email, and messaging availability;
- operational minimum age, fan-verification requirement, anonymous adult-preview rule, required assurance, re-verification interval, and grace;
- creator identity, creator adult verification, payout KYC, co-performer verification, and release requirements;
- public/restricted preview controls;
- provider adapter name and a non-secret provider policy reference.

These are configuration fields, not legal conclusions. `minimum_age` does not establish a person's DOB and `provider_policy_key` must not contain a credential.

## Feature revisions

Feature flags are append-only overlays evaluated after inherited country rules. The resolver selects the highest currently effective country-scoped revision; if absent, it selects the highest global revision. A disabled country-specific feature therefore wins over a global enable without changing the base jurisdiction revision.

`platform_access=false` is the one maintenance/global access switch. There is no second competing maintenance authority. New paid actions also require `purchases`; a domain enable cannot bypass the umbrella purchase control.

## Resolution order

For each request/command the resolver:

1. validates and reconciles current trusted country signals, using valid provider provenance only when current authority is absent;
2. requires an enabled country registry row;
3. loads the highest reviewed effective country revision and its reviewed effective template;
4. merges explicit country overrides;
5. applies the country/global feature revision;
6. evaluates anonymous-preview policy when relevant;
7. evaluates the current verification against configured threshold, assurance, expiry, re-verification, and grace;
8. returns one structured decision.

Uncertainty denies: missing country, conflict, disabled country, absent/invalid/unreviewed policy, disabled feature, missing/expired/revoked/insufficient verification, or provider outage never becomes `allowed=true`.

Age and entitlement remain independent. A verified adult without a subscription/PPV entitlement remains locked. A purchaser whose verification is expired remains unable to receive adult-restricted media. Admin, manager, creator ownership, and support roles do not implicitly bypass viewer-age policy.

`explicit_public_preview_allowed` is an enforced policy input: when false, public preview projection and final preview delivery fail closed. `restricted_media_policy` is currently an opaque, reviewed reference reserved for a future named media-policy/classification adapter. It is recorded and visible for planning and audit, but it does not change runtime access by itself and must not be presented as an enforcement toggle.

## Admin workflow

At `/admin/compliance`:

1. register the country code, which remains disabled by default;
2. create a named policy template;
3. create a complete immutable template revision with effective dates, review state, demo marker, and reason;
4. create a country revision selecting that exact template revision and only the intended overrides;
5. enable the country only after its reviewed policy is effective;
6. optionally create global/country feature revisions;
7. use Countries to inspect the flattened effective policy;
8. use the real Policy Simulator for representative users/features and restricted-media cases;
9. run provider diagnostics and inspect audit before launch.

The UI starts policy construction from a safe blocked draft. Creating a country revision requires an explicit confirmation that inherited rules, overrides, dates, and weakening warning were reviewed. Backend permissions and validation remain authoritative.

Country enable/disable, template/revision creation, country revisions, feature revisions, verification review, and provider probes are audited. The ISO catalogue is seeded disabled; only explicitly enabled countries are advertised publicly, activation refuses a country without a reviewed effective policy, and production readiness independently checks every enabled country. Policy history is append-oriented and cannot be rewritten through ordinary admin operations.

## Demo configuration

Development seed data is fictional and marked demo:

- PT is the local baseline with the deterministic test provider and self-attested minimum assurance;
- GB demonstrates a stronger `medium` assurance and short re-verification interval;
- US demonstrates disabled purchase and Marketplace features.

These rows exist only to exercise inheritance, stronger-policy re-verification, feature denial, admin simulation, and UI states. They are not statements about PT, GB, US, or any law. Production readiness rejects active demo policy/template/feature rows.

## Production checklist

- Obtain qualified legal review and create non-demo country policy revisions.
- Decide which countries are enabled; do not infer policy from the ISO registry.
- Configure trusted GeoIP proxy/header behavior and an intentional fallback strategy.
- Test signal conflicts, missing signals, blocked countries, per-feature blocks, and policy transitions.
- Configure reviewed effective dates and validate old/new results around each boundary.
- Configure the production age provider and prove a fresh healthy callback diagnostic.
- Preview effects on representative existing users before a stronger assurance or shorter re-verification change.
- Publish applicable legal versions and validate mandatory acceptance independently.
- Keep retention, provider evidence handling, and incident response as explicit externally reviewed policies.

Do not hardcode country lists or age rules in React, product services, or media routes. Do not deploy the fictional demo rules.
