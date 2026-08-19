# Implementation Roadmap

Phased implementation and engineering acceptance gates. Codex should implement one bounded phase/domain at a time.

# 42. Implementation Roadmap

| Phase | Scope |
| --- | --- |
| 0 - Foundation | Repository, environments, auth, roles, PostgreSQL, Alembic, API conventions, audit skeleton, tests, CI. |
| 1 - Creators | Creator profiles, verification state, follows, settings, permission foundations. |
| 2 - Content & Media | Assets, galleries, videos, secure previews, content access policies. |
| 3 - Ledger & Payments | Transactions, double-entry ledger, purchase orchestration, commissions, payouts foundation. |
| 4 - Subscriptions & Promotions | 1/3/6/12 month products, independent promotion engine, renewals and entitlements. |
| 5 - Social | Feed, posts, likes/comments/reactions, Stories/Highlights, auto-promotion settings, fan levels, badges, streaks, optional leaderboards and loyalty foundations. |
| 6 - Messaging | DM permissions, paid messages, PPV attachments, mass messaging. |
| 7 - Streaming | Public rooms, chat, tips, private 1:1, 2-to-1, session billing. |
| 8 - Groups/Agencies | Invitations, delegated permissions, versioned contracts, immutable historical splits. |
| 9 - Marketplace | Products, orders, fulfilment/privacy, digital and physical products. |
| 10 - Discovery | Search, filters, ranking, recommendations foundation. |
| 11 - Featuring | Surfaces, slot inventory, bookings, paid placements. |
| 12 - Referral/Affiliate | Attribution, rewards, earnings and fraud controls. |
| 13 - Trust & Safety expansion | Reporting, moderation cases, appeals, consent/release workflows. |
| 14 - Analytics/BI | Creator, group and platform dashboards and attribution. |

# 43. Codex Engineering Rules

These rules should later be mirrored into AGENTS.md and PROJECT.md.

1. Read the authoritative project documents before making changes.
1. Do not invent new business rules when an existing specification is ambiguous; preserve extensibility and document assumptions in code/PR notes.
1. Do not store financial truth in mutable balance fields; use the ledger.
1. Do not retroactively modify accepted group splits or historical transaction pricing.
1. Do not put financial calculations or permission decisions only in the frontend.
1. Do not expose original private media to create previews.
1. Every schema change requires an Alembic migration and test coverage appropriate to risk.
1. Every payment/webhook flow must be idempotent.
1. Every admin/manager sensitive action must produce an audit event.
1. Prefer modular monolith boundaries; avoid cross-module direct DB manipulation where a domain service exists.
1. Use explicit enums/state machines rather than loosely interpreted strings.
1. Add tests for the business invariant before or with implementation.

# 44. Critical Acceptance Tests / Business Invariants

- Creator A joins Group X at 50/50. Group default later becomes 30/70. Creator A still settles at 50/50 until accepting a new version.
- Creator B joins after the default changes to 30/70 and settles at 30/70.
- A €20 PPV purchase with 20% platform fee and 50/50 creator/group contract produces €4 platform, €8 creator, €8 group under the defined basis.
- A creator can set 30% off on all subscription durations.
- A creator can set 40% off only on the 3-month product while 1/6/12 remain unchanged.
- A creator can set different discounts for 1, 3, 6 and 12 months simultaneously.
- A future promotion edit does not change the recorded price of a completed purchase.
- An unauthorised viewer can fetch the video preview but cannot obtain the original paid asset URL.
- Leaving a group removes future manager authority but does not rewrite historical transactions.
- A duplicated payment-provider webhook does not create duplicate entitlement or ledger postings.
- Every visible content object that supports user reporting can create a report tied to the exact object/version/context.
- Admin impersonation produces an audit trail and cannot silently bypass high-risk restrictions.
- A Story can expire from public visibility after its lifecycle while remaining available in required moderation/audit records.
- A creator can save an expired Story to a persistent Highlight without changing the original Story lifecycle.
- Story previews/teasers linking to PPV content never expose the original locked media asset.
- Realtime live filters can be disabled/degraded without interrupting the underlying broadcast or billing session.
- Story and Live effects operate on derivatives/processing instructions and do not silently overwrite original creator media.

# 47. Immediate Next Step

Do not ask Codex to build the entire platform from this document in one prompt. Repository governance documents are now defined. Implement `PHASE_0_FOUNDATION.md` next, using `TECH_STACK.md` as the Phase 0 technology authority, with tests and intentional migrations. Each later phase should be given its own scoped specification and acceptance criteria derived from this blueprint.
