# Phase 6 implementation notes

Phase 6 adds a bounded, server-authoritative messaging foundation. A creator/viewer pair has one unique `Conversation`, with normalized `ConversationParticipant` rows so later staff or group participants can be added without changing the thread identity. Inbox reads use participant state; messages are chronological by `(created_at, id)` and are soft-removable rather than hard-deleted.

Creator settings resolve eligibility on the server: anyone, followers, active subscribers, previous customers, or nobody. A previous customer has a settled PPV purchase or a completed subscription period; failed/cancelled attempts never qualify. Blocks are platform-wide user relationships and prevent both directions of direct messaging and mass delivery. Mute/archive are retained as per-party inbox state.

Message attachments reference existing private `MediaAsset` rows. A locked attachment stores its immutable price/currency at creation. It is purchased through the existing `PaymentAttempt`, verified provider webhook, immutable ledger accounts, commission calculation, and creator-pending earnings path. A message unlock grants access only to that asset; it cannot unlock unrelated creator content. Locked media delivery remains gated by the central media access resolver.

Mass campaigns snapshot eligible recipients at execution time and create a durable campaign-recipient row with a unique campaign/recipient constraint before each delivery. Replays therefore do not duplicate recipients. Campaign execution is deliberately bounded to the worker boundary in follow-up hardening; scheduled rows are durable UTC state and require the scheduled worker invocation.

Current bounded limitations: paid *send* confirmation is exposed as a server-resolved quote but its payment command is not yet enabled; creator attachment composition, report moderation UI/context access, websocket fan-out, and frontend inbox screens remain follow-up work within Phase 6. No Stories, Live, groups, marketplace, or notification delivery is introduced.
