# Phase 12 implementation notes

## Foundation

Paid featuring is a server-authoritative inventory domain. `FeatureSurface`, `FeatureSlot`, and append-only versioned `FeaturePrice` records define the only bookable paid inventory. A `FeatureBooking` snapshots its selected payer, actor, target owner, slot, UTC window, duration, price, currency, pricing version, and cancellation cutoff; later price or policy changes cannot modify that history.

Bookings create a short durable reservation while awaiting payment. Slot availability is checked under a slot row lock and includes active/scheduled bookings and unexpired reservations. Featuring charges reuse the existing payment attempt and immutable ledger: payment settlement posts one balanced `featuring_charge` transaction from platform clearing to platform revenue. Featuring is never creator or group revenue and does not use creator/group split allocation.

Target ownership and eligibility are resolved server-side. Creators can feature their own approved public profile, published non-moderated content, purchasable marketplace listings, and currently live rooms. A group manager requires the explicit `manage_featuring` grant; the booking snapshots both manager audit actor and selected payer. A manager cannot silently charge the creator: only the selected payer may initiate payment.

Organic discovery ranking remains a distinct Phase 11 concern. The foundation deliberately has no organic-score field or ranking write path; later sponsored insertion consumes active bookings as a separately labelled layer after organic eligibility/filtering.

## Lifecycle and sponsored serving

The scheduled worker expires unpaid reservations, activates due paid bookings only after revalidating the target, and deactivates expired placements from durable UTC timestamps. Current serving repeats the same eligibility check and fails closed. Platform/moderation ineligibility produces one compensating immutable refund using floor-rounded `price_minor * unused_seconds / duration_seconds`; a placement never activated receives its full snapshot price. Creator-ended live/target termination stops serving but issues no automatic refund. Pre-start cancellation uses the booking's snapshotted cutoff: cancellation at or before the cutoff fully refunds, while later pre-start cancellation does not.

Sponsored insertion happens only after the Phase 11 organic candidate/filter/rank pipeline. A paid target must already be an eligible organic candidate for the exact query and filters; the insertion then replaces its organic occurrence at the server-owned slot position and carries explicit `sponsored`, `placement_type`, and `sponsored_surface` response metadata. It cannot alter an organic score, bypass blocks or moderation, or appear for irrelevant search input.

Payment webhooks settle each booking once through existing event replay protection and ledger idempotency. A chargeback creates one immutable `chargeback` reversal of the exact platform-revenue booking transaction and stops the placement. The booking slot lock makes concurrent last-slot reservations deterministic: one transaction reserves it and the other fails cleanly.

Sponsored impressions, clicks, and conversions are distinct `discovery_events` event types, request-deduplicated and annotated with the booking/surface identifiers. They do not update organic click/impression events and never write referral attribution. Marketplace inventory must remain published and purchasable at serving time; sold-out listings fail closed. A live-room placement likewise requires an actively live room; a creator ending that room stops the placement without an automatic refund, while platform-side interruption follows the compensating unused-time refund policy.
