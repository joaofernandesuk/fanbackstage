# Technical Architecture

System structure, domain boundaries, reliability requirements and infrastructure principles.

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
| Jobs | Celery + Redis initially |
| Object storage | S3-compatible |
| Media processing | FFmpeg workers |
| Realtime app events | FastAPI WebSockets + Redis |
| Interactive streaming | LiveKit self-hosted (WebRTC/SFU) |
| Broadcast playback | LiveKit initially; HLS/LL-HLS delivery path when scale warrants |
| Search | PostgreSQL initially; OpenSearch when justified |
| Observability | Structured logs + OpenTelemetry-compatible metrics/tracing + error reporting |


For detailed pinned Phase 0 technology decisions, provider boundaries and streaming topology, see `TECH_STACK.md`.

## 33.2 Media security

- Private object storage by default.
- Signed short-lived URLs or authorised proxy delivery.
- Separate preview derivatives.
- Transcoding pipeline states.
- Virus/malware scanning where relevant.
- Metadata stripping where appropriate.
- Watermarking optional by product tier/policy.
- CDN configured to respect access-control architecture.

# 35. Core Data Model Domains

| Domain | Representative entities |
| --- | --- |
| Identity | User, RoleAssignment, Session, SecurityEvent |
| Creator | CreatorProfile, CreatorSettings, VerificationStatus |
| Content | Content, MediaAsset, Video, Gallery, GalleryItem, Post, BlogPost, Story |
| Entitlements | AccessPolicy, Entitlement, PurchaseEntitlement, SubscriptionEntitlement |
| Subscription | SubscriptionProduct, SubscriptionPriceVersion, Subscription, Renewal |
| Promotion | Promotion, PromotionTarget, PromotionEligibility, Redemption |
| Commerce | Order, OrderItem, MarketplaceProduct, Shipment |
| Messaging | Conversation, Message, MessageAttachment, PaidMessageOffer |
| Streaming | LiveRoom, LiveSession, PrivateSession, Participant, TipMenuItem |
| Groups | Group, Membership, Contract, ContractVersion, PermissionGrant |
| Finance | LedgerAccount, LedgerEntry, Transaction, Payout, Refund, Dispute |
| Featuring | PlacementSurface, Slot, Booking, PromotionPurchase |
| Referral | ReferralProgramme, ReferralCode, Attribution, ReferralEarning |
| Moderation | Report, ModerationCase, ModerationAction, Appeal |
| Audit | AuditEvent |

# 37. Important State Machines


## 36.1 Content

```text
draft -> processing -> pending_review -> published
                       -> rejected
published -> restricted -> restored / removed
published -> archived
```


## 36.2 Subscription

```text
pending -> active -> past_due -> active
                 -> cancelled_at_period_end -> expired
                 -> suspended / terminated
```


## 36.3 Group contract

```text
invited -> proposed -> accepted -> active -> ended
                    -> rejected
                    -> expired
```


## 36.4 Payout

```text
requested -> review/queued -> processing -> paid
                          -> failed -> retry / cancelled
```

# 38. APIs and Domain Boundaries

Business rules must live in backend domain/application services, not duplicated in React components. APIs should expose commands and resolved representations rather than allowing clients to calculate financial results.

- Purchase endpoint calls pricing + entitlement + payment orchestration.
- Ledger service is the only component allowed to post financial entries.
- Group contract service resolves active contract version.
- Commission resolver returns effective rule/version.
- Entitlement service decides whether a user can access an asset.
- Promotion engine decides eligibility and effective subscription price.
- Media service produces preview/transcoded derivatives.
- Moderation service controls publish/restrict status.

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

# 41. Non-Functional Requirements

- Scalability: streaming/media can scale independently from web application.
- Reliability: idempotent writes for payments, scheduling and webhooks.
- Performance: CDN media delivery, caching and paginated APIs.
- Observability: trace purchase/settlement from user action to provider event to ledger.
- Privacy: minimise exposure of addresses, identity data and private content metadata.
- Accessibility: core UI designed with keyboard/screen-reader considerations.
- Internationalisation: text, currency and timezone foundations prepared for multiple markets.
