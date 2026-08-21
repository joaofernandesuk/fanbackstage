# Live Streaming and Realtime Creator Studio

Public Live, private sessions, 2-to-1/multi-party rooms, realtime effects, billing and interactive engagement.

## Phase 7 public-live/private-session policy

Private-session requests may queue while a creator is public live, but the creator cannot accept or start one until the public room has been explicitly ended. FanBackstage does not automatically end a public room, pause it, or move viewers to a holding state. A private request remains queued and the creator dashboard must state that ending the public live is required before acceptance. A private-break / be-right-back mode is deferred to a future streaming enhancement.

Public transport uses short-lived LiveKit tokens issued only after FanBackstage's PostgreSQL authorization decision. Creator tokens may publish; public-viewer tokens are subscribe-only. Durable chat and REST polling remain available if realtime transport degrades. Public-recording requests are persisted for provider egress processing; private sessions deliberately have no recording command and remain unrecorded by default. Live report context requires moderation permission and is audited.

# 9. Live Webcam and Streaming


## 9.1 Public live rooms

- Free or gated room entry according to creator settings.
- Realtime chat.
- Tipping and configurable tip menu.
- Virtual gifts.
- Paid requests where permitted.
- Private-session request flow and queue.
- Creator-defined live title, category, tags and schedule.
- Report control available during live viewing.


## 9.2 Private sessions

- 1 viewer : 1 creator.
- Per-minute billing and optional minimum session duration.
- Optional fixed-price session types.
- Pre-authorisation/credit reservation to avoid unpaid session time.
- Automatic stop or downgrade when available balance cannot cover the next billing unit.
- Session events recorded for billing and dispute audit without unnecessarily retaining sensitive audiovisual data.


## 9.3 2-to-1 and multi-party private rooms

- 2 viewers : 1 creator as a first-class product.
- Architecture should later support N viewers : 1 creator.
- Also prepare for 1 viewer : 2 creators and collaborative shows.
- Pricing can be fixed, per minute, or per viewer per minute.
- Multi-creator revenue allocation must use an explicit show split agreement, not ad-hoc manual calculations.


## 9.4 Live Creator Studio, filters and realtime effects

Creators should be able to prepare and control a live broadcast through a dedicated Live Creator Studio. This should include:

- Camera/microphone device selection and pre-live preview.
- Stream title, category, tags, thumbnail/cover and audience/access settings.
- Free, followers-only, subscribers-only or otherwise permitted gated live modes.
- Tip menu, goals, pinned offers and private-session availability.
- Realtime visual filters and lightweight effects.
- Beauty/softening controls where technically supported.
- Background blur/replacement or privacy-friendly masking where supported.
- Text overlays, banners, timers, tip goals, subscriber goals and pinned messages.
- Stickers/reactions and platform-managed overlay assets.
- Polls, Q&A and interactive audience prompts.
- Moderator/co-host controls and multi-creator collaboration where permitted.
- Ability to promote a subscription, PPV item, gallery, video, marketplace product or other campaign during the live.
- Optional scene/preset configurations that can be saved and reused.

Realtime filters/effects must have explicit performance budgets and graceful degradation. A client or device that cannot support an effect must still be able to broadcast reliably without it. Heavy post-processing suitable for uploaded Stories/videos must not automatically be assumed safe for realtime WebRTC.

## 9.5 Live events, replay and clips

Where enabled by creator/platform settings and consent policy, a live session may create a replay/VOD derivative or short promotional clips. Recording must be explicit, visible to participants where required, and separated from ordinary billing/session telemetry. A replay can subsequently use the normal Content access system (Free, Followers, Subscribers, PPV, Private, etc.).

## 9.6 Streaming technology boundaries

Use WebRTC where low-latency interactive communication is required and HLS/LL-HLS or equivalent for scalable broadcast distribution where appropriate. Streaming signalling, media servers and recording policy should remain isolated from the core application domain. Media effects should be implemented as a distinct realtime processing layer rather than coupling filters to financial/live-room business logic.

# 10. Tipping and Virtual Gifts

Tips should be available at creator profile, feed post, image/gallery, video, blog, live, private live, message and other relevant surfaces.

- Preset tip amounts plus custom amount.
- Creator-configurable live tip menu.
- Virtual gifts may map to fixed credit/value amounts.
- Tip transactions must be ledger entries with source context.
- Tips may be refundable/reversible only according to explicit fraud/payment policy; never mutate ledger history.

## Gamification integration

Gamification is a cross-platform engagement layer spanning profiles, feed, Stories, Lives, messaging, subscriptions, purchases and referrals. It must increase retention without obscuring real prices, manipulating users into spending, or creating a second unaudited financial system.
- Reactions on feed posts, Stories, Lives, videos, galleries, blog posts and other supported surfaces.
- Realtime Live reactions may be ephemeral visual events while durable aggregate counts are stored asynchronously.
- Live attendance and engagement.
- Top Live Creator
- Priority placement in permitted Live request queues.
- Regular creator Live attendance.
## 19.6 Leaderboards and top supporters
Creators may enable leaderboards for selected periods such as current Live, day, week, month or campaign.
- Live supporters.
- Referral leaderboard.
- Community participation leaderboard.
- Creator can disable leaderboards entirely.
- Monetary leaderboards use settled/eligible ledger data, never raw client counters.
## 19.7 Goals, milestones and community progress
Extend the existing Wishlist/Goals module with gamified progress components:
- Creator tip/funding goals.
- Live room goals.
- Community unlock goals.
A goal may unlock a creator-defined action or platform entitlement, but completion must never imply an unmoderated or prohibited obligation. Goal contribution events reference the immutable ledger.
First Live
100 Live Viewers
- Priority Live request access.
- Fraud/abuse detection for points, referrals, gifts and leaderboards.
- Enable/disable public leaderboards.
- Opt out of public leaderboard identity.
LeaderboardDefinition
LeaderboardPeriod
LeaderboardEntry
Gamification consumes canonical domain events from purchases, subscriptions, Lives, content engagement and referrals. It must not become the source of truth for those domains.
- Public leaderboard opt-out never removes legitimate private accounting/progress records.
