# Phase 10 implementation notes

## Attribution foundation

Phase 10 begins with a separate attribution domain: affiliate partners, referral programs, immutable versioned commission policies, opaque referral links, privacy-minimised touches, and one immutable signup attribution per user.

The public `/api/v1/r/{code}` route accepts only validated internal destinations. It records a bounded, session-hashed touch and issues an HTTP-only, HMAC-signed first-party attribution token. Registration verifies that token server-side and snapshots the selected policy; browser-provided referrer IDs are never trusted.

The current policy defaults are 30 days for attribution and 90 days for subscription reward eligibility. Referral commission is exclusively a future share of platform commission and must never change creator or group allocations. Creator-to-creator program rows are intentionally paused: no financial reward is implemented until a milestone policy is approved.

## Dashboard reconciliation

The authenticated referral dashboard resolves creator, user and affiliate ownership only on the server. It exposes only programmes, links, conversions and allocation rows belonging to that authenticated account. Pending, available and reversed totals are calculated from immutable `referral_commission_allocations` lifecycle snapshots, each attached to its source ledger transaction; current policy values are never used to recompute historical earnings. The permanent regression suite verifies those totals against the referral ledger account and confirms that a later policy edit leaves the dashboard unchanged.
