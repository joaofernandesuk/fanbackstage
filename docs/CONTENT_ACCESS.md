# Content Access and Entitlements

All content gating, preview and durable purchase access must flow through these rules.

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

Media audience and entitlement are independent checks. `safe_public` assets may expose
only approved derivatives through the normal public resolver. `adult_restricted` assets
also require a current server-resolved adult-access decision, even when the viewer owns a
subscription, PPV, or message entitlement. Buying or following never satisfies the age
boundary. Anonymous decisions come only from the server-signed acknowledgement cookie;
restricted presigned URLs cannot outlive that cookie. Original storage keys and original
media URLs remain private in both classifications.

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
- For every non-free video, the configured preview interval and rendered trailer must end strictly before the protected source ends. A short source can never silently make the full playback derivative its public trailer.
- Creator preview start/duration changes are accepted only before review and render asynchronously. Content cannot enter review while that selected derivative is unavailable.
- Video purchase grants a durable entitlement subject to platform policy, refunds/chargebacks and legal takedown conditions.
- Creator can publish teasers separately from the paid master asset.

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

Phase 5 resolves feed access through the same server-side entitlement boundary: followers access requires a persisted follow, subscription access requires the existing creator-scoped subscription entitlement, and PPV content references retain their independent purchase entitlement. Locked feed representations must omit protected post body and attached media.


## 13.1 Automatic feed promotion

- Automatically announce new videos.
- Automatically announce new galleries.
- Automatically announce live streams.
- Automatically announce blog posts.
- Automatically announce marketplace products.
- Creator can enable/disable each category globally and override per publication.


## 13.2 Following and discovery feeds

Provide Following and Discover/For You experiences. Initial ranking can rely on deterministic signals before machine-learning complexity: recency, engagement, followed categories, creator interactions, prior purchases, live status, language, popularity and featured placements. Sponsored/featured ranking must remain distinguishable from organic ranking internally and, where required, visibly.

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

# 16. Content Bundles and Collections

Creators can package multiple existing entitlements into one commercial bundle without duplicating media.

```text
Summer Collection
3 videos + 5 galleries
Individual total: €79
Bundle price: €49
```

Bundle purchase grants entitlement to each included asset according to bundle rules and retains purchase provenance for refunds/accounting.
# Phase 3 PPV integration

PPV access is granted only by an active `ContentEntitlement` whose purchase source references the settled purchase. A payment attempt, client success message, or ledger record alone never grants content access. A full PPV refund revokes that entitlement without deleting it.

Baseline platform eligibility is checked before every new paid reservation or payment
attempt, including safe-public marketplace items. This does not classify a physical item
as adult media; anonymous marketplace browsing remains separate from the authenticated
18+ transaction boundary. Idempotent replays of an already-created action remain replays
and do not create new value movement.
# Message attachment access

Paid message attachments are distinct from content PPV. The central media resolver permits a full asset only for the creator owner, an existing authorised content policy, or the recipient's settled message-unlock purchase for that exact attachment. A message unlock never grants access to unrelated galleries or videos.
