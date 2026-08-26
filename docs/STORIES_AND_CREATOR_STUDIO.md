# Stories and Creator Studio

Ephemeral Stories, Highlights, filters/effects, interactive stickers, monetisation and analytics.

# 14. Stories and Creator Studio

Stories are a first-class social and monetisation format, not merely a temporary feed post. The platform should provide a mobile-first Creator Studio for recording, composing, editing, previewing and publishing Stories.

## Implemented authoritative slice (2026-08-26)

The current bounded implementation stores one creator-owned media card per `Story` row and groups those rows by creator in consumer presentation. Publication is immediate and has an exact 24-hour lifecycle. The authoritative states are `active`, `expired`, soft-deleted `deleted`, and moderator-contained `removed`; expiry and removal take a Story out of consumer queries while retaining its row and audit history. Creation accepts only an approved creator's ready image or video asset and reuses the existing media pipeline. A Story asset must not already be referenced by content, posts, messages, or marketplace listings, so Story publication cannot broaden another domain's access contract. Delivery is restricted to the authorised image `display` or video `preview_clip` derivative; originals and full playback derivatives are never projected by the Story API. Public media URL lifetime is capped below the remaining Story lifetime.

The implemented access policies are `free`, `followers`, and `subscription`, resolved server-side against creator eligibility, two-way blocks, follow state, active creator-scoped entitlements, media readiness, and moderation state. `ppv` and `private` are rejected until a Story-specific purchase/direct-share contract exists. Creator creation requires a creator-scoped `Idempotency-Key`; the same key and payload returns the original Story, while key reuse with different input is rejected. Creator listing/deletion, consumer rail/detail/media delivery, reporting/moderator removal, deterministic cursor ordering, and replay-safe scheduled expiry are available through existing API domains. Replies, reactions, view analytics, drafts/scheduling, text-only or multi-card authoring, editor effects, PPV linking, and Highlights remain future work; the consumer viewer does not fabricate those interactions.

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

Highlights are not part of the current bounded Story implementation. They require their own durable selection/order/access contract and must not be inferred from expired Story rows.

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
