# Data Model

Canonical domain entities and state relationships. Detailed fields belong in migrations/models but must follow these concepts.

## Phase 15 notifications

Notification intents are durable, deduplicated delivery requests. In-app notifications and
delivery attempts are derived channel records; preferences and hashed-email suppressions are
communication controls, never alternative identity stores.

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
| Social | Follow, CreatorFeedSettings, FeedPost, FeedPostMedia, PostReaction, PostComment, PostMention, Hashtag, SocialReport |

`User.adult_attested_at` and `adult_attestation_version` are a paired record of the
platform's current baseline 18+ self-attestation. They are not a date of birth,
jurisdiction decision, identity verification, or provider-backed age-assurance result.
An authenticated account must hold the current policy version before a new paid action
or adult-restricted delivery is authorised.

`MediaAsset.audience` is moderator-owned and fail-closed: new and migrated assets are
`adult_restricted` until an authorised reviewer explicitly classifies them
`safe_public`. Actual classification changes are audited; replaying the same decision
does not create another event. Creator verification remains separate: every public
creator-owned projection requires the latest `CreatorVerification` outcome to be
`verified` with `adult_verified = true`.

# 37. Important State Machines


## 36.1 Content

```text
draft -> processing -> pending_review -> published
                       -> rejected
published -> restricted -> restored / removed
published -> archived
```

`MediaAsset` records a bounded `processing_attempts` counter. A transient storage failure remains `queued` only while the worker has a real retry available; the final attempt becomes `failed`. Other processing failures become `failed` immediately. A failed asset may return to `queued` only through the owner-authorised recovery workflow while below the configured maximum; every processing attempt remains observable and derivative rows stay unique per asset/type. Video preview regeneration follows the same queued-while-retryable and failed-at-limit rule, and an explicit regeneration can restore its unique preview derivative without changing the protected source.

Payment retry associations are append-only domain history. `PurchasePaymentAttempt`,
`SubscriptionRenewalAttempt`, and `FeatureBookingPaymentAttempt` retain a positive,
monotonic attempt number and a unique payment-attempt reference while the parent
keeps the current authoritative attempt pointer. `PaymentRefundRequirement` is a
generic, one-per-attempt record for excess captures across PPV, subscriptions,
marketplace, featuring, messaging and private live. It freezes the source,
amount, currency, liability ledger transaction, eventual compensating reversal
and provider resolution reference; it is not an entitlement or mutable balance.


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

The implemented `stories` table owns lifecycle and publication state while referencing the existing `creator_profiles`, `users`, and `media_assets` tables. A row records creator ownership, publishing user, creator-scoped idempotency key, media asset, caption/alt text, access policy, `published_at`, exact `expires_at = published_at + 24 hours`, and the `active`/`expired`/`deleted`/`removed` state timestamps. It does not duplicate storage keys or create a Story-specific media object. The referenced asset is Story-safe and cannot simultaneously inherit another content/post/message/marketplace delivery contract. Expired, deleted, and moderation-removed rows remain durable; consumer queries include only active, unexpired, eligible records. Highlights are not yet represented by a table or inferred from Story state.
# Phase 3 financial core

The Phase 3 financial tables are `ledger_accounts`, `ledger_transactions`, `ledger_entries`, `commission_rules`, `payment_attempts`, `purchases`, `purchase_payment_attempts`, `payment_refund_requirements`, and `payment_webhook_events`. A purchase is canonical per buyer/content and snapshots allocation once. `purchase_payment_attempts` preserves each positive, ordered provider attempt so a failed charge can be retried with a new idempotency key without creating another purchase; a replay of any prior key resolves to its original attempt. The first verified success from that history becomes the canonical settlement attempt. Any later successful capture is barred from creating another ledger transaction or entitlement and instead creates one frozen, attempt-unique `payment_refund_requirements` row in `required` state for provider-refund operations. Subscription periods use the corresponding durable attempt history for both initial and renewal retries, and only the current subscription-period attempt may mutate or settle that period. Ledger entries are currency-specific, positive minor-unit postings and must balance by transaction.

# Phase 11 discovery

`discovery_configs` is an append-oriented versioned ranking configuration. `discovery_hides` is an auditable operational exclusion overlay for otherwise public entities. `discovery_events` stores deduplicated, privacy-minimised discovery analytics. They are derived controls and never replace creator, content, marketplace, live, entitlement, moderation, referral, or ledger truth.
