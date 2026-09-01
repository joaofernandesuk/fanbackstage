# Live Streaming and Realtime Creator Studio

Public Live, private sessions, 2-to-1/multi-party rooms, realtime effects, billing and interactive engagement.

## Phase 7 public-live/private-session policy

Private-session requests may queue while a creator is public live, but the creator cannot accept or start one until the public room has been explicitly ended. FanBackstage does not automatically end a public room, pause it, or move viewers to a holding state. A private request remains queued and the creator dashboard must state that ending the public live is required before acceptance. A private-break / be-right-back mode is deferred to a future streaming enhancement.

Public transport uses short-lived LiveKit tokens issued only after FanBackstage's PostgreSQL authorization decision. Creator tokens may publish; public-viewer tokens are subscribe-only. Durable chat and REST polling remain available if realtime transport degrades. Public-recording requests are persisted for provider egress processing; private sessions deliberately have no recording command and remain unrecorded by default. Live report context requires moderation permission and is audited.

Token expiry is capped by the shortest current FanBackstage authority bound (age/compliance evidence, creator KYC, and subscription entitlement where applicable) and by a hard five-minute maximum. Token expiry is not treated as a connected-client revocation mechanism. Ending or moderating a room calls LiveKit `DeleteRoom`; banning or losing viewer authority calls `RemoveParticipant`; creator suspension and verification revocation invoke the same provider controls immediately. A scheduled reconciliation repeats those decisions after jurisdiction, feature-policy, entitlement, creator-KYC, or age-verification changes. Because self-hosted LiveKit can accept a still-cached token after removal, signed join callbacks re-check PostgreSQL authority, re-remove banned/denied participants, and re-delete any terminal room recreated with a cached token. Provider-control failures are audited and retained for retry rather than being mistaken for successful enforcement.

Provider controls also have a transactional PostgreSQL outbox foundation. A domain transaction can enqueue an exact `delete_room` or `remove_participant` intent with its room, participant (when applicable), source target, reason, actor, and idempotency key. A separate worker can see the intent only after that transaction commits, takes a bounded processing lease with `FOR UPDATE SKIP LOCKED`, commits the lease before contacting LiveKit, and records success or a capped-backoff retry in a new transaction. Successful provider control invokes an idempotent domain-success hook in the same transaction that records outbox success, so a deleted room cannot remain indefinitely `ending` and a private session can settle atomically. Finalization is fenced to the owning attempt; a delayed earlier success cannot overwrite a newer attempt's pending/failure state. LiveKit's missing-resource-safe delete/remove operations make a repeated call safe if the provider succeeded but the final database update failed. Provider and network failures remain retryable; only a structurally invalid persisted command is terminal.

Paid requests use creator-configured, server-owned options and snapshot their commercial terms. A verified provider payment moves an acceptance-required request to the creator queue; it is not revenue until accepted. Acceptance creates one settlement and one canonical Live event. Decline, expiry, and a capture confirmed after expiry use the finance domain's durable excess-capture refund requirement rather than deleting or rewriting history. Request text is length-bounded, rate-limited, and remains subject to central Trust & Safety reporting.

Live reactions use a small server enum and aggregate room/type counters under an aggressive dedicated rate limit; individual clicks do not become permanent activity events. The current-session supporter ranking is calculated only from unreversed, ledger-backed canonical Live tip, gift, and accepted-request events. It has no editable leaderboard record. Activity, goals, reaction aggregates, and supporter ranking are refetched from PostgreSQL after joining or reconnecting, so the browser can replace transient state without duplicating financial events.

Live reporting resolves through the central Trust & Safety target snapshot and report queue. Participant removal is a central enforcement action that audits the actor and enqueues the existing durable LiveKit `remove_participant` control. The creator cannot be removed as a participant; room termination is the explicit creator-level control. Neither report handling nor removal mutates settled ledger history.

The server-control JWTs are method-scoped: room deletion receives only `roomCreate`; participant listing/removal receives `roomAdmin` plus the exact room name. Browser tokens never receive either administrative grant.

Private staging supplies only a remote `wss://` endpoint, API key/secret, and an operator-confirmed signed webhook registration; this repository does not provision LiveKit. The access gateway may exempt the exact webhook path from MFA, but it must preserve the raw body and authorization header so application signature verification remains mandatory. Production additionally needs reviewed TLS/TURN, capacity, region and recording controls.

## Local LiveKit integration and E2E transport

On macOS, local LiveKit runs natively on the host via `scripts/livekit-local.sh`: Docker Desktop cannot reliably bridge host-browser WebRTC media traffic into a container. The development server exposes signalling on `ws://localhost:17880`, TCP fallback on `17881`, and UDP mux on `17882`; it binds and advertises only `127.0.0.1`. Browsers receive that public loopback URL. Because a container cannot safely reach that loopback-only control plane, `scripts/livekit-control-local.sh` runs the dedicated `live_control` Celery queue on the host and connects to the exposed development PostgreSQL/Redis ports. The general container worker explicitly does not consume that queue. Deployed environments route the same durable task through the normal scheduled worker and may optionally use `FANBACKSTAGE_LIVEKIT_CONTROL_URL` when their private control route differs from the browser URL. Signed webhooks target `http://localhost:18000`. This is intentionally local-only: deployed environments must advertise their own externally reachable RTC address and provide appropriate UDP/TCP/TURN infrastructure.

The real-stack Playwright configuration grants camera/microphone permissions to the isolated frontend origin and launches Chromium with deterministic fake-media flags. The Phase 7 journey verifies that synthetic creator A/V is actually published, a subscribe-only viewer renders the video track, and a viewer token does not contain publish authority. No browser-side presence or media mock is used.

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
# Terminal callback safety

LiveKit callbacks are signed and their provider IDs are persisted before private-session state changes. A callback delivered after a session is ending, ended, or settled is retained for replay auditing but is a no-op; it cannot reopen the session, restart billing, or alter the final settlement.
