# Phase 5 implementation notes

Phase 5 adds a modular `social` domain. `Follow` is a normalized, unique user-to-public-creator relationship and remains independent of subscription state. The centralized content resolver now uses it for existing `followers` content.

`FeedPost` references existing `ContentItem` and `MediaAsset` records; it never copies galleries, videos, originals, or entitlements. Post access is resolved server-side: free is public, followers requires `Follow`, subscription requires the existing creator-scoped subscription entitlement, and PPV remains represented by its referenced content and Phase 3 purchase flow. Locked post payloads omit body and attached media.

Posts use explicit `draft`, `scheduled`, `published`, `archived`, and `removed` states. The scheduled worker locks due rows and can be safely replayed. The cursor is `(published_at, id)` in descending order. Discover is deterministic recency ordering; Following joins only the viewer's follows. One pin per creator is enforced by the command which clears the existing pin.

Reactions have one active enum value per user/post. Comments are one-level threaded; deleted comments are soft deleted. Mention and hashtag indexing is server-side and only indexes public approved creators. Reports retain stable post/comment target identifiers; full moderation-case workflow remains Phase 13.

Discover and Following cursors encode the complete feed ordering tuple: pin timestamp (or unpinned), publication timestamp, and post UUID. This prevents duplicates or skips for equal publication timestamps while preserving pin ordering.

Creator settings control gallery/video announcements. Approval invokes the feed domain and `source_content_id` uniqueness makes announcement replay idempotent. A nullable `feed_announcement_override` on existing content has precedence over creator defaults. The creator studio supports text/media/reference composition, scheduling, post pin/archive controls, and access-aware viewer cards; no new media upload or PPV pipeline was introduced.

Social writes have a Redis-backed, configurable rate limit partitioned by action (follow, post, reaction, comment and report). The throttle is applied in addition to — never instead of — server-side ownership and access checks. Moderators can inspect, dismiss, and remove/hide report targets with audit events; full case workflow remains deferred.
