# Phase 10 implementation notes

## Attribution foundation

Phase 10 begins with a separate attribution domain: affiliate partners, referral programs, immutable versioned commission policies, opaque referral links, privacy-minimised touches, and one immutable signup attribution per user.

The public `/api/v1/r/{code}` route accepts only validated internal destinations. It records a bounded, session-hashed touch and issues an HTTP-only, HMAC-signed first-party attribution token. Registration verifies that token server-side and snapshots the selected policy; browser-provided referrer IDs are never trusted.

The current policy defaults are 30 days for attribution and 90 days for subscription reward eligibility. Referral commission is exclusively a future share of platform commission and must never change creator or group allocations. Creator-to-creator program rows are intentionally paused: no financial reward is implemented until a milestone policy is approved.

## Dashboard reconciliation

The authenticated referral dashboard resolves creator, user and affiliate ownership only on the server. It exposes only programmes, links, conversions and allocation rows belonging to that authenticated account. Pending, available and reversed totals are calculated from immutable `referral_commission_allocations` lifecycle snapshots, each attached to its source ledger transaction; current policy values are never used to recompute historical earnings. The permanent regression suite verifies those totals against the referral ledger account and confirms that a later policy edit leaves the dashboard unchanged.

## Real-stack release coverage

The Playwright referral scenario creates creator and affiliate programmes through the restricted super-admin surface, follows the opaque first-party link before registration, and validates marketplace allocation states across pending, available and reversed. It proves that marketplace rewards remain pending until the order is releasable, that release moves the exact allocation, that refund and chargeback reverse exact historical allocations, and that a second affiliate account cannot inspect the first affiliate's dashboard. The isolated E2E operator is explicitly granted `super_admin`; production authorization is unchanged.

## Security review

Referral dashboard ownership is never accepted from a request parameter: the authenticated user determines its creator profile, user beneficiary rows and affiliate partners before any allocation, link or conversion query runs. The public referral route accepts opaque link codes only and records attribution in a signed HTTP-only cookie; registration consumes that server-verified state once. Admin programme, policy, link and affiliate mutations retain the restricted `financial.configure` check, while dashboard UI calls only the authenticated `/r/me/dashboard` projection. Cross-affiliate access is covered both at the domain boundary and through the browser scenario.
