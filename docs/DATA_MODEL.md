# Data Model

Canonical domain entities and state relationships. Detailed fields belong in migrations/models but must follow these concepts.

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

## Cross-domain invariants

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
