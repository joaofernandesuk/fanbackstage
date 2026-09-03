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
| Streaming | LiveRoom, LiveSession, PrivateSession, Participant, TipMenuItem, LiveProviderControlIntent |
| Groups | Group, Membership, Contract, ContractVersion, PermissionGrant |
| Finance | LedgerAccount, LedgerEntry, Transaction, Payout, Refund, Dispute |
| Featuring | PlacementSurface, Slot, Booking, PromotionPurchase |
| Referral | ReferralProgramme, ReferralCode, Attribution, ReferralEarning |
| Moderation | Report, ModerationCase, ModerationAction, Appeal |
| Audit | AuditEvent |
| Social | Follow, CreatorFeedSettings, FeedPost, FeedPostMedia, PostReaction, PostComment, PostCommentReaction, PostMention, Hashtag, SocialReport |
| Compliance | CountryRegistry, CompliancePolicyTemplate/Revision, JurisdictionPolicyRevision, FeatureFlagRevision, AnonymousComplianceSession, AgeVerificationRecord, AgeProviderCallbackEvent, AgeProviderProbe |
| Performer | PerformerIdentity, PerformerIdentityVerification, PerformerAgeVerification, VerifiedContentPerformer, ConsentRelease |
| Legal | LegalDocument, LegalDocumentVersion, LegalAcceptance, SiteSettingsVersion |

`User.adult_attested_at` and `adult_attestation_version` are a paired record of the
platform's current baseline 18+ self-attestation. They are not a date of birth,
jurisdiction decision, identity verification, or provider-backed age-assurance result.
An authenticated account must hold the current policy version before a new paid action
or adult-restricted delivery is authorised.

`MediaAsset.audience` is moderator-owned and fail-closed: new and migrated assets are
`adult_restricted` until an authorised reviewer explicitly classifies them
`safe_public`. Actual classification changes are audited; replaying the same decision
does not create another event. Creator verification remains separate: every public
creator-owned projection requires the latest current `CreatorVerification` outcome to
satisfy normalized identity and adult requirements. Fan age assurance never supplies
creator KYC.

## Compliance and legal authority

`CountryRegistry` provides canonical ISO identifiers and an operational enabled state; it contains no legal rule. Full `CompliancePolicyTemplateRevision` rows are immutable reusable rule sets. `JurisdictionPolicyRevision` inherits one exact template revision and stores only explicit country overrides. `FeatureFlagRevision` is a global/country append-only overlay. Each revision has an effective window, version, demo marker, actor/reason, and review evidence where applicable. Higher effective successor versions win without rewriting history.

`AgeVerificationRecord` belongs to either a user or durable `AnonymousComplianceSession` and snapshots the country, exact jurisdiction-policy ID/version, provider, required/achieved threshold and assurance, lifecycle status/times, safe failure code, retryability, and minimal normalized result. It stores no DOB, document image, access token, or whole provider payload. Callback events and provider probes provide replay/operational evidence without credentials. Anonymous attachment is one-user-only and row-locked.

`PerformerIdentity` is private and separate from a creator profile. Identity and age verification are independent append-oriented records. `VerifiedContentPerformer` links each required private performer to content and its own consent release; all linked performers must independently satisfy the current requirements.

`LegalDocument` is stable scope (type, slug, audience, language, optional country). Draft `LegalDocumentVersion` bodies are editable; published/retired bodies are immutable. `LegalAcceptance` references one exact version. `SiteSettingsVersion` is an append-only current/superseded public configuration record rather than a general executable CMS.

`LiveProviderControlIntent` is the durable DB-to-LiveKit control boundary. It stores one exact `delete_room` or `remove_participant` command, source target, provider room/identity, reason, actor, unique idempotency key, attempt/lease schedule, safe error evidence, and pending/processing/succeeded/structurally-failed state. Provider and network errors return to retryable pending state with capped backoff; they never become terminal merely because an attempt limit was reached. A processing lease is committed before the provider call, and the attempt-fenced domain-success hook commits related room/session finalization together with outbox success.

`User.country_code` is an account signal, not the sole jurisdiction authority. Current KYC/billing/trusted-request/account authorities must agree or access fails closed. A valid provider record's country remains immutable evidence provenance and supplies jurisdiction only when current authority is absent; after a current country change, its threshold/assurance is evaluated against the new policy and may require stronger re-verification. The earlier paired `adult_attested_at`/version remains only a compatibility `self_attested` assurance and is never upgraded into provider, creator, or performer verification.

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

Phase 7 Live commerce keeps creator-owned `LivePaidRequestOption` rows separate from immutable request snapshots on `LiveCommerceCharge`. Acceptance-required charges move from verified payment to a creator decision before their single settlement/event can exist. `LiveReactionAggregate` stores one bounded counter per room and reaction type; it is engagement state, not a canonical financial event. Current-room supporter rankings are queries over eligible ledger-linked `LiveEvent` rows and therefore have no editable leaderboard table.


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

`creator_profile_media` assigns at most one creator-owned, ready, approved `safe_public` image to each avatar or cover slot and stores bounded focal coordinates. Public delivery resolves only the processed display derivative; it never promotes or exposes the original object. Private 2-to-1 requests persist an explicit invitation state and response timestamp. `live_goals.starts_at` is the immutable query baseline for ledger-derived progress after a creator reset.

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

The implemented `stories` table owns lifecycle and publication state while referencing the existing `creator_profiles`, `users`, and `media_assets` tables. A row records creator ownership, publishing user, creator-scoped idempotency key, media asset, caption/alt text, access policy, `published_at`, exact `expires_at = published_at + 24 hours`, and the `active`/`expired`/`deleted`/`removed` state timestamps. It does not duplicate storage keys or create a Story-specific media object. The referenced asset is Story-safe and cannot simultaneously inherit another content/post/message/marketplace delivery contract. `StoryReaction` records one current reaction per viewer and Story; it has no independent visibility or access authority, and reaction commands re-resolve the Story's active public access before writing. Expired, deleted, and moderation-removed rows remain durable; consumer queries include only active, unexpired, eligible records. Highlights are not yet represented by a table or inferred from Story state.
# Phase 3 financial core

The Phase 3 financial tables are `ledger_accounts`, `ledger_transactions`, `ledger_entries`, `commission_rules`, `payment_attempts`, `purchases`, `purchase_payment_attempts`, `payment_refund_requirements`, and `payment_webhook_events`. A purchase is canonical per buyer/content and snapshots allocation once. `purchase_payment_attempts` preserves each positive, ordered provider attempt so a failed charge can be retried with a new idempotency key without creating another purchase; a replay of any prior key resolves to its original attempt. The first verified success from that history becomes the canonical settlement attempt. Any later successful capture is barred from creating another ledger transaction or entitlement and instead creates one frozen, attempt-unique `payment_refund_requirements` row in `required` state for provider-refund operations. Subscription periods use the corresponding durable attempt history for both initial and renewal retries, and only the current subscription-period attempt may mutate or settle that period. Ledger entries are currency-specific, positive minor-unit postings and must balance by transaction.

# Phase 11 discovery

`discovery_configs` is an append-oriented versioned ranking configuration. `discovery_hides` is an auditable operational exclusion overlay for otherwise public entities. `discovery_events` stores deduplicated, privacy-minimised discovery analytics. They are derived controls and never replace creator, content, marketplace, live, entitlement, moderation, referral, or ledger truth.
