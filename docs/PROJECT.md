# Project Context

High-level project definition, product boundary, implementation strategy and immediate delivery sequence.

# 1. Product Vision and Non-Negotiable Principles

The product must be designed as a modular creator-economy platform rather than as a webcam site with features bolted on. A single identity can participate as viewer, creator, manager or administrator according to permissions.

- One account can hold multiple roles; creators may also purchase from other creators.
- All monetisation flows ultimately settle through a unified, auditable ledger.
- Content access is policy-driven, not hard-coded by content type.
- Creator ownership and autonomy must survive agency/group membership changes.
- Historical financial terms are immutable: later configuration changes cannot rewrite prior earnings.
- Every sensitive administrative action must be auditable.
- Trust, age/identity verification, performer consent and reporting are foundational, not post-launch add-ons.
- Media originals must never be exposed to unauthorised clients merely because a preview exists.
- Platform commission, group split, promotion cost and payment-processing effects must remain separate accounting concepts.

# 2. Roles, Identity and Account Model

| Role | Capabilities |
| --- | --- |
| Viewer / Fan | Discover creators; follow; subscribe; buy PPV; tip; message; join live/private sessions; purchase marketplace items; refer users. |
| Creator / Model | Publish and monetise content; stream; sell products; blog; manage subscribers; receive tips; use promotions; join/leave groups. |
| Group / Agency Manager | Manage authorised creator profiles, content, messaging, schedules and analytics within explicit delegated permissions. |
| Moderator | Review reports, content queues, sanctions and verification tasks without unrestricted financial/admin powers. |
| Admin | Platform operations, support, content review, user access, settings and reporting according to permission scope. |
| Super Admin | Restricted high-trust role for platform-wide configuration, permissions, finance and critical operations. |

```text
User
 ├── ViewerProfile
 ├── CreatorProfile
 ├── ManagerProfile
 └── RoleAssignments / Permissions
```

Authentication should be account-centric. Role checks must be enforced server-side. Front-end hiding is never sufficient authorisation.

# 34. Technical Architecture

Recommended initial architecture: modular monolith with strict module boundaries. Avoid premature microservices but isolate high-throughput/media concerns so they can be extracted later.

```text
backend/
  accounts/
  creators/
  verification/
  content/
  media/
  feed/
  subscriptions/
  pricing_promotions/
  payments/
  wallet_ledger/
  payouts/
  streaming/
  messaging/
  marketplace/
  groups/
  referrals/
  affiliates/
  featuring/
  moderation/
  notifications/
  analytics/
  search/
  admin_ops/
  audit/
```


## 33.1 Suggested stack

| Layer | Recommendation |
| --- | --- |
| Backend | FastAPI + Python |
| ORM | SQLAlchemy 2 |
| Database | PostgreSQL |
| Migrations | Alembic |
| Frontend | Next.js + React + TypeScript |
| Cache/ephemeral state | Redis |
| Jobs | Celery, Dramatiq or equivalent |
| Object storage | S3-compatible |
| Media processing | FFmpeg workers |
| Realtime | WebSockets |
| Interactive streaming | WebRTC |
| Broadcast playback | HLS/LL-HLS where appropriate |
| Search | PostgreSQL initially; OpenSearch when justified |
| Observability | Structured logs + metrics + tracing + error reporting |


## 33.2 Media security

- Private object storage by default.
- Signed short-lived URLs or authorised proxy delivery.
- Separate preview derivatives.
- Transcoding pipeline states.
- Virus/malware scanning where relevant.
- Metadata stripping where appropriate.
- Watermarking optional by product tier/policy.
- CDN configured to respect access-control architecture.

# 42. Implementation Roadmap

| Phase | Scope |
| --- | --- |
| 0 - Foundation | Repository, environments, auth, roles, PostgreSQL, Alembic, API conventions, audit skeleton, tests, CI. |
| 1 - Creators | Creator profiles, verification state, follows, settings, permission foundations. |
| 2 - Content & Media | Assets, galleries, videos, secure previews, content access policies. |
| 3 - Ledger & Payments | Transactions, double-entry ledger, purchase orchestration, commissions, payouts foundation. |
| 4 - Subscriptions & Promotions | 1/3/6/12 month products, independent promotion engine, renewals and entitlements. |
| 5 - Social | Feed, posts, likes/comments, stories, auto-promotion settings. |
| 6 - Messaging | DM permissions, paid messages, PPV attachments, mass messaging. |
| 7 - Streaming | Public rooms, chat, tips, private 1:1, 2-to-1, session billing. |
| 8 - Groups/Agencies | Invitations, delegated permissions, versioned contracts, immutable historical splits. |
| 9 - Marketplace | Products, orders, fulfilment/privacy, digital and physical products. |
| 10 - Discovery | Search, filters, ranking, recommendations foundation. |
| 11 - Featuring | Surfaces, slot inventory, bookings, paid placements. |
| 12 - Referral/Affiliate | Attribution, rewards, earnings and fraud controls. |
| 13 - Trust & Safety expansion | Reporting, moderation cases, appeals, consent/release workflows. |
| 14 - Analytics/BI | Creator, group and platform dashboards and attribution. |

# 47. Immediate Next Step

Do not ask Codex to build the entire platform from this document in one prompt. First create the repository-level governance documents, then implement Phase 0/Foundation with tests and migrations. Each later phase should be given its own scoped specification and acceptance criteria derived from this blueprint.
