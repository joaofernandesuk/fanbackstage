# Product Specification

Authoritative product behaviour. Technical implementation must preserve these user-facing and commercial rules.

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

# 3. Creator Profile

- Username and display name
- Avatar and cover
- Bio and optional location
- Languages
- Categories/tags
- Orientation/preferences fields where appropriate
- Verified status
- Online/offline/live indicators
- Subscription products and prices
- Follower and subscriber counts
- Content counts
- Wishlist/goals
- Marketplace storefront
- Blog
- Feed
- Videos
- Galleries
- Live schedule
- Optional ratings/reviews if later enabled

Primary profile actions: Follow, Subscribe, Message, Tip, Watch Live, Buy Content, Shop, Favourite/Bookmark and Report.

# 4. Unified Content Architecture

Content access must be modelled independently from content type. A video, gallery, post, blog article or live event can each use different access rules.

```text
Content
 ├── Post
 ├── Video
 ├── Gallery
 ├── Live / LiveEvent
 ├── BlogPost
 ├── Audio (future)
 └── Story
```


## 4.1 Access policies

- FREE
- FOLLOWERS_ONLY
- SUBSCRIBERS
- PPV
- PRIVATE / DIRECT_SHARE
- GROUP / AUDIENCE_SEGMENT
- BUNDLE_ENTITLEMENT

Access policies should resolve through an entitlement service. The same asset may participate in subscription access, PPV, bundle ownership or a direct paid message without duplicating the original file.

# 5. Galleries and Image Monetisation

- Creators may create galleries with arbitrary image counts.
- Gallery may be free, follower-only, subscription-included, PPV, private/direct, or sold in a bundle.
- Creator chooses preview image count or specific preview images.
- Locked images are represented by authorised derivative/blur states, not by exposing original file URLs.
- First image can serve as the default commerce preview unless creator selects another.
- Gallery can be sold independently even when the creator also offers subscriptions.

# 6. Video Monetisation and Secure Previews

- Videos may be free, follower-only, subscription-included, PPV, private/direct or bundled.
- Creator may define a preview duration, e.g. first 20 seconds, or choose a custom time range.
- Backend generates a separate preview derivative via media processing; unauthorised clients never receive the original source URL.
- Video purchase grants a durable entitlement subject to platform policy, refunds/chargebacks and legal takedown conditions.
- Creator can publish teasers separately from the paid master asset.

# 7. Subscription Products, Durations and Promotions

Subscriptions must support independent pricing and promotions by duration. Pricing and promotion are separate entities so creators are not constrained to one global discount.


## 7.1 Base duration products

| Duration | Example base price | Independent configuration |
| --- | --- | --- |
| 1 month | €14.99 | Yes |
| 3 months | €39.99 | Yes |
| 6 months | €69.99 | Yes |
| 12 months | €119.99 | Yes |


## 7.2 Promotion engine - mandatory behaviour

A creator may apply a promotion to all subscription durations, to only one duration, to any subset of durations, or define a different promotion for each duration.

| Example | 1 month | 3 months | 6 months | 12 months |
| --- | --- | --- | --- | --- |
| Promotion A - all plans | 30% off | 30% off | 30% off | 30% off |
| Promotion B - one plan only | No promotion | 40% off | No promotion | No promotion |
| Promotion C - different by plan | 20% off | 25% off | 35% off | 45% off |
| Promotion D - mixed | 15% off | No promotion | 30% off | 50% off |

The promotion object should support percentage discount and, if desired later, fixed promotional price. It should reference one or more specific subscription products rather than assuming a single creator-wide percentage.


### Promotion eligibility and controls

- New subscribers only.
- All eligible subscribers.
- Former/expired subscribers for reactivation campaigns.
- Specific audience segment or campaign code if later enabled.
- Start date/time and end date/time.
- Optional maximum redemptions globally or per user.
- Optional first billing period only versus recurring promotional cycles, subject to product rules.
- Optional auto-renew behaviour after the promotional period, clearly disclosed before purchase.
- Stacking rules: default should be no stacking unless explicitly supported.
- Timezone handling must be deterministic and stored in UTC internally.


### Pricing history

Every completed subscription purchase must store the exact product version, base price, discount, final price, currency, commission settings and other settlement inputs used at purchase time. Changing future prices or promotions must not rewrite historical transactions.

For an active recurring subscription, the system must define explicit policy for whether a future creator price change applies at renewal, requires notice, or only applies to new purchases. The implementation must not silently infer this.

# 8. PPV Independent of Subscription

Being subscribed does not automatically mean access to every creator asset. A creator can include normal content in the subscription while selling premium videos, galleries, messages, bundles or custom content separately.

```text
Example
Subscription: €9.99 / month
Included posts: subscription entitlement
Premium Gallery: €12 PPV
Premium Video: €25 PPV
Custom Content Request: €100+
```

# 10. Tipping and Virtual Gifts

Tips should be available at creator profile, feed post, image/gallery, video, blog, live, private live, message and other relevant surfaces.

- Preset tip amounts plus custom amount.
- Creator-configurable live tip menu.
- Virtual gifts may map to fixed credit/value amounts.
- Tip transactions must be ledger entries with source context.
- Tips may be refundable/reversible only according to explicit fraud/payment policy; never mutate ledger history.

# 13. Feed and Social Layer

The feed combines creator social posting with monetisation and discovery.

- Text
- Photo
- Video
- Gallery
- Poll
- Link
- Live announcement
- Blog article
- Marketplace item
- Promotion/offer

Every post can independently be Free, Followers, Subscribers, PPV or other supported entitlement type.


## 13.1 Automatic feed promotion

- Automatically announce new videos.
- Automatically announce new galleries.
- Automatically announce live streams.
- Automatically announce blog posts.
- Automatically announce marketplace products.
- Creator can enable/disable each category globally and override per publication.


## 13.2 Following and discovery feeds

Provide Following and Discover/For You experiences. Initial ranking can rely on deterministic signals before machine-learning complexity: recency, engagement, followed categories, creator interactions, prior purchases, live status, language, popularity and featured placements. Sponsored/featured ranking must remain distinguishable from organic ranking internally and, where required, visibly.

Creator categories are optional discovery interests, not account roles or content-permission labels. The initial catalogue is tailored to adult-creator work: solo performances, couples and collaborations, glamour and lingerie, fetish and kink, cosplay and fantasy, live shows, photo sets, video and behind-the-scenes, audio and ASMR, fitness and body confidence, roleplay and characters, and custom content. Creators select searchable interests as removable chips; the platform may use them for discovery only. They never replace consent, verification, jurisdiction, age-assurance, or access-policy decisions.

# 14. Stories and Creator Studio

Stories are a first-class social and monetisation format, not merely a temporary feed post. The platform should provide a mobile-first Creator Studio for recording, composing, editing, previewing and publishing Stories.

## 14.1 Story formats and lifecycle

- 24-hour ephemeral format by default, with configurable platform policy for expiry.
- Photo, short video, text, multi-card and mixed-media Story variants.
- Draft, scheduled, published, expired, archived and removed states.
- Free, followers-only, subscribers-only, teaser/PPV-linked and other supported entitlement modes.
- Creators can manually archive Stories before expiry.
- Expiration removes normal public visibility without necessarily deleting moderation, financial or audit evidence.
- Story view events should support analytics without exposing viewer information beyond platform privacy rules.

## 14.2 Story creation tools, filters and effects

The Creator Studio should support non-destructive editing so that an original asset can be retained separately from the rendered Story derivative. Supported tools should include:

- Crop, rotate, trim and playback-speed controls where appropriate.
- Brightness, contrast, saturation, warmth, sharpness and similar basic adjustments.
- Preset visual filters/LUT-style looks managed by the platform.
- Beauty/softening effects where technically supported and permitted.
- Background blur and other privacy-friendly visual effects.
- Text overlays with font, alignment, size and animation options.
- Stickers, emoji, GIF-like platform assets and decorative overlays.
- Creator-defined captions and subtitles.
- Polls, questions, Q&A, countdowns and other interactive stickers.
- Links/deep links to creator profile, subscription offer, video, gallery, live room, blog, marketplace product or campaign.
- @mentions and hashtags.
- Music/audio overlays only through content for which the platform has appropriate rights/licensing; architecture must not assume unrestricted commercial music use.
- Optional branded templates for creators/groups and platform campaigns.
- Save draft and duplicate/reuse a Story design.

Effects and filters must be represented as edit instructions/metadata where possible rather than destructively modifying the original upload. A publish/render job should create the delivery derivative used by viewers.

## 14.3 Story monetisation and promotion

Stories can:

- Tease or deep-link to a paid video/gallery.
- Advertise a subscription or a duration-specific subscription promotion.
- Announce an upcoming or currently active live session.
- Promote a marketplace item, blog article, bundle or custom-content offer.
- Contain a direct Tip action.
- Act as a preview for locked content without leaking the original paid asset.

## 14.4 Story Highlights

Creators may optionally convert archived Stories into persistent Highlights displayed on the profile. Highlights can have a title, cover, ordering and access policy. Removing a Story from its original 24-hour lifecycle must not automatically remove an explicitly saved Highlight unless the underlying content is moderated or deleted.

## 14.5 Story engagement and analytics

Support, subject to product/privacy policy:

- Views and unique viewers.
- Completion rate across multi-card Stories.
- Forward/back/exit interactions.
- Replies/reactions.
- Link/product/content clicks.
- Tips and purchases attributed to a Story.
- Subscription conversions attributed to a Story or Story campaign.

All paid conversions must be attributed through durable transaction/campaign references rather than mutable analytics counters.

# 15. Creator Blogs

- Rich text editor
- Images and video
- Galleries
- Scheduling
- Tags/categories
- Comments if enabled
- Free, subscriber or PPV access
- Share/promote to feed
- SEO/public discovery options for explicitly public posts

# 16. Content Bundles and Collections

Creators can package multiple existing entitlements into one commercial bundle without duplicating media.

```text
Summer Collection
3 videos + 5 galleries
Individual total: €79
Bundle price: €49
```

Bundle purchase grants entitlement to each included asset according to bundle rules and retains purchase provenance for refunds/accounting.

# 17. Custom Content Requests

- Creator-configurable request menu and prices.
- Options such as duration, personalisation, priority delivery and other allowed customisations.
- Workflow: request -> quote/acceptance -> payment/authorisation -> production -> delivery -> completion/dispute.
- Clear platform prohibitions and moderation controls.
- Expiry/cancellation rules for unaccepted or undelivered requests.

# 18. Wishlist, Goals and Fan Funding

- Creator-defined goals with target and progress.
- Birthday or equipment wishlist.
- Contributions recorded independently from ordinary tips if product/legal treatment differs.
- Goal completion must not imply off-platform obligation unless explicitly supported.

# 19. Gamification, Loyalty and Social Engagement

Gamification is a cross-platform engagement layer spanning profiles, feed, Stories, Lives, messaging, subscriptions, purchases and referrals. It must increase retention without obscuring real prices, manipulating users into spending, or creating a second unaudited financial system.

## 19.1 Reactions and lightweight social engagement

- Reactions on feed posts, Stories, Lives, videos, galleries, blog posts and other supported surfaces.
- Platform-defined reaction catalogue with moderation-safe assets; creator-specific reaction packs may be introduced later.
- Replies/comments remain separate from reactions for moderation and notification purposes.
- Realtime Live reactions may be ephemeral visual events while durable aggregate counts are stored asynchronously.
- Creator can disable reactions/comments on supported content where product policy allows.

## 19.2 Fan levels and creator community loyalty

Each creator may optionally enable a creator-specific fan/loyalty programme. A viewer's level with Creator A is independent from their level with Creator B.

Possible qualifying signals include:

- Length of follow/subscription relationship.
- Subscription renewals and tenure.
- Purchases of PPV content, marketplace products or bundles.
- Tips and virtual gifts.
- Live attendance and engagement.
- Participation in creator-approved challenges, polls or community actions.
- Referral outcomes where permitted.

The system should use configurable weighted points/XP rather than hard-coding spending as the only path to status. Non-monetary engagement must be possible so loyalty does not become purely pay-to-rank.

Example creator-specific levels:

```text
New Fan
Regular
Supporter
VIP
Superfan
```

Creators may rename tiers within platform policy and choose whether levels are visible publicly, privately to the fan, or disabled.

## 19.3 Creator levels, reputation and achievement badges

The platform may separately operate creator progression/reputation based on verified and quality-related signals such as:

- Identity/performer verification completion.
- Account age and good standing.
- Publishing consistency.
- Subscriber retention.
- Response-rate/service metrics where appropriate.
- Successful marketplace fulfilment.
- Moderation history and policy compliance.
- Community engagement.

Badges must distinguish factual/verified achievements from promotional status. A paid Featured placement must never masquerade as an earned trust badge.

Possible badge types:

- Verified Creator
- New Creator
- Trending
- Top Live Creator
- Reliable Seller
- Community Favourite
- Anniversary/Milestone badges
- Platform campaign/event badges

## 19.4 VIP status and creator-defined benefits

Creators can optionally define loyalty benefits for fans who reach a level or satisfy a rule. Benefits may include:

- Profile/community badge.
- Subscriber-only/VIP-only post or Story access.
- Priority placement in permitted Live request queues.
- Early access to content or live schedules.
- Creator-defined discounts on eligible products.
- Free or discounted paid-message entry according to rules.
- Access to VIP group/community areas when implemented.
- Exclusive polls, Q&A or giveaways where legally permitted.

A loyalty benefit is an entitlement/rule, not an informal front-end flag. Any financial discount must be represented by a versioned pricing/promotion rule so settlement remains auditable.

## 19.5 Streaks

Optional streaks can reward healthy recurring engagement such as:

- Consecutive subscription months.
- Regular creator Live attendance.
- Creator posting streaks.
- Creator response/service consistency.
- Platform check-in/community participation where appropriate.

Streaks must be optional and must not use deceptive loss-pressure patterns. Missing a day/event should not silently remove purchased entitlements or paid value.

## 19.6 Leaderboards and top supporters

Creators may enable leaderboards for selected periods such as current Live, day, week, month or campaign.

Possible rankings:

- Top tippers.
- Top gift senders.
- Most engaged supporters.
- Live supporters.
- Referral leaderboard.
- Community participation leaderboard.

Important rules:

- Creator can disable leaderboards entirely.
- Users can opt out of public display; private/anonymised ranking should be supported.
- Monetary leaderboards use settled/eligible ledger data, never raw client counters.
- Refunds, chargebacks and fraud adjustments affect eligibility through explicit reconciliation rules.
- Platform must avoid exposing sensitive exact lifetime spending unless the user has explicitly chosen that visibility.

## 19.7 Goals, milestones and community progress

Extend the existing Wishlist/Goals module with gamified progress components:

- Creator tip/funding goals.
- Live room goals.
- Subscriber milestones.
- Follower milestones.
- Community unlock goals.
- Launch/event countdowns.

A goal may unlock a creator-defined action or platform entitlement, but completion must never imply an unmoderated or prohibited obligation. Goal contribution events reference the immutable ledger.

## 19.8 Achievements and challenges

The platform may define achievements for creators and fans, for example:

```text
First Subscription
12-Month Supporter
First Live
100 Live Viewers
100 Subscribers
1,000 Followers
First Marketplace Sale
Top Supporter - August
Creator Anniversary
```

Achievements should be event-driven and idempotent: replaying analytics events must not create duplicate rewards.

Optional creator/community challenges may use an explicit start/end window, qualification criteria and reward definition.

## 19.9 Loyalty rewards and reward catalogue

Rewards may be non-monetary entitlements or platform/creator-funded economic benefits.

Examples:

- Badge or cosmetic profile treatment.
- Exclusive Story/Highlight.
- Free content entitlement.
- Discount code/promotion entitlement.
- Priority Live request access.
- Free/discounted subscription period where commercially supported.
- Creator-defined digital reward.

Do not introduce an untracked shadow currency. If the platform later adds points, credits or redeemable rewards with monetary value, issuance, expiry, redemption and reversal must be ledgered and clearly separated from cash balance.

## 19.10 Membership tiers beyond the base subscription

The architecture should allow optional creator-defined membership tiers in the future without requiring them in the MVP. Example:

```text
Supporter      €9.99/month
VIP            €24.99/month
Inner Circle   €49.99/month
```

Each tier can map to an explicit set of entitlements and can itself support the duration/pricing/promotion engine (1, 3, 6 and 12 months) if the platform enables multi-tier subscriptions. Tier upgrades/downgrades, prorating and renewal changes require explicit state and billing rules rather than ad-hoc price replacement.

## 19.11 Gamification controls and safety

Platform controls:

- Enable/disable individual gamification features globally.
- Set allowed badge/reward types and financial limits.
- Prevent prohibited or misleading promotions/rewards.
- Moderate names/assets used for creator-defined tiers or challenges.
- Fraud/abuse detection for points, referrals, gifts and leaderboards.

Creator controls:

- Enable/disable creator-specific fan levels.
- Choose supported qualifying signals within platform limits.
- Configure eligible rewards/benefits.
- Enable/disable public leaderboards.
- Enable/disable streaks and selected achievements.
- Choose public/private display of selected community badges.

Fan controls:

- Opt out of public leaderboard identity.
- Control display of badges/status where supported.
- View how status was earned and what benefits currently apply.

## 19.12 Gamification data model and event boundaries

Suggested entities:

```text
GamificationProgram
GamificationRule
FanCreatorProgress
AchievementDefinition
AchievementAward
BadgeDefinition
BadgeAssignment
StreakDefinition
StreakState
LeaderboardDefinition
LeaderboardPeriod
LeaderboardEntry
RewardDefinition
RewardGrant
Challenge
ChallengeProgress
LoyaltyEntitlement
```

Gamification consumes canonical domain events from purchases, subscriptions, Lives, content engagement and referrals. It must not become the source of truth for those domains.

## 19.13 Gamification acceptance invariants

- The same financial transaction cannot award the same idempotent achievement/reward twice.
- A refunded/charged-back purchase follows explicit reward reversal policy; history remains auditable.
- Public leaderboard opt-out never removes legitimate private accounting/progress records.
- Paid Featured status is never represented as an earned verification/reputation badge.
- Creator-specific fan progress cannot leak across creators.
- A loyalty discount produces a versioned pricing record and exact settlement snapshot.
- Disabling a gamification feature does not delete historical financial/audit evidence.
- No reward system may bypass content entitlement, moderation or age/identity controls.

# 20. Featured Placement and Promotion Marketplace

The platform can sell time-bound ranked placements for creator profiles and individual content.


## 19.1 Featureable objects

- Creator profile
- Webcam/live profile
- Video
- Gallery
- Feed post
- Marketplace item
- Blog article


## 19.2 Slot model

- Named surface/location, e.g. homepage, category page, live listing.
- Position number, e.g. #1, #2, #3.
- Start/end time.
- Hourly or daily inventory.
- Fixed platform-defined price initially.
- Availability lock/booking state to prevent double-booking.
- Creator/group purchase attribution.


## 19.3 Future auction option

The architecture may later support bidding for high-value slots. Do not implement auctions in the MVP unless requested, but avoid data models that make time-slot bidding impossible.

# 25. Referral and Affiliate Systems


## 24.1 Referral types

- Fan -> Fan: credits/reward to referrer and optionally referred user.
- Creator -> Creator: e.g. percentage of platform revenue generated by referred creator for a defined period.
- Agency referral: separate business terms.
- Campaign-specific referral incentives.

Creator referral rewards should preferably be funded from the platform commission rather than silently reducing the referred creator's agreed earnings unless terms explicitly say otherwise.


## 24.2 Referral tracking

- Referral code/link
- Click and signup attribution
- Verification conversion
- Qualified/active creator conversion
- Revenue generated
- Commission earned
- Attribution window
- Self-referral and fraud controls
- Reversal policy for refunded/charged-back activity


## 24.3 Affiliate programme

Affiliate functionality should be separate from simple referral incentives and support tracking links, campaign IDs, sub IDs, creatives, conversion reports, attribution windows, payouts and fraud controls.

```text
?ref=ABC123&campaign=twitter&subid=ad42
```

# 26. Notifications and Communication Preferences

- New follower
- New subscriber
- Renewal/expiry
- Tip
- Message
- PPV purchase
- Live/private request
- Marketplace order
- Referral conversion
- Group invitation
- Contract proposal
- Payout status
- Content moderation outcome
- Report/support update

Delivery channels may include in-app, email and push, with granular preference controls except where transactional/legal notices cannot be disabled.

# 27. Search, Discovery and Ranking

- Creators
- Live Now
- Videos
- Galleries
- Posts
- Blogs
- Marketplace

Filters may include category, price, live status, language, verified status, popularity, recent activity, newest and featured. Ranking should expose enough internal telemetry to explain whether an item appeared organically or due to paid featuring.

# 28. Scheduling and Creator Calendar

- Posts
- Videos
- Galleries
- Blogs
- Mass messages
- Promotions
- Live sessions
- Featured slots

A calendar view should expose scheduled, draft, published, expired and failed states. Scheduled jobs must be idempotent and safe to retry.

# 29. Creator Content Vault

Creators and authorised group managers need an asset library independent of publication surfaces.

- Photos and videos
- Tags/folders/collections
- Used versus unused assets
- Previously sold/published references
- Free versus paid usage metadata
- Search/filter
- Duplicate/hash awareness
- Rights/performer metadata links
- Manager access limited by delegated permission
