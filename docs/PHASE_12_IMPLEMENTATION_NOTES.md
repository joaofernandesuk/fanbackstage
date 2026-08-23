# Phase 12 implementation notes

## Foundation

Paid featuring is a server-authoritative inventory domain. `FeatureSurface`, `FeatureSlot`, and append-only versioned `FeaturePrice` records define the only bookable paid inventory. A `FeatureBooking` snapshots its selected payer, actor, target owner, slot, UTC window, duration, price, currency, pricing version, and cancellation cutoff; later price or policy changes cannot modify that history.

Bookings create a short durable reservation while awaiting payment. Slot availability is checked under a slot row lock and includes active/scheduled bookings and unexpired reservations. Featuring charges reuse the existing payment attempt and immutable ledger: payment settlement posts one balanced `featuring_charge` transaction from platform clearing to platform revenue. Featuring is never creator or group revenue and does not use creator/group split allocation.

Target ownership and eligibility are resolved server-side. Creators can feature their own approved public profile, published non-moderated content, purchasable marketplace listings, and currently live rooms. A group manager requires the explicit `manage_featuring` grant; the booking snapshots both manager audit actor and selected payer. A manager cannot silently charge the creator: only the selected payer may initiate payment.

Organic discovery ranking remains a distinct Phase 11 concern. The foundation deliberately has no organic-score field or ranking write path; later sponsored insertion consumes active bookings as a separately labelled layer after organic eligibility/filtering.
