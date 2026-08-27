# Featured Placement and Promotion Inventory

Time-bound paid discovery inventory for profiles, live rooms and content, separated from organic ranking and trust badges.

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

## Phase 12 implementation boundary

Phase 12 implements fixed, versioned platform pricing only. A surface owns named
slots with capacity and a booking reserves one slot/window under a database lock.
Bookings snapshot the payer, requesting actor, target owner, slot/window, price
and price version, currency, and cancellation cutoff. Those snapshots are never
rewritten by later configuration changes.

Provider attempts are append-only history beneath that one booking. A verified
failure moves the booking to `failed` while its original bounded reservation
continues to consume inventory; it becomes retryable only until that reservation
expires. Replaying the same payment `Idempotency-Key` returns its original attempt,
while a deliberate retry requires a new key and creates a new attempt without
repricing or extending the reservation. The first valid provider success owns the
single Featuring settlement. Any later/stale successful capture creates the shared
finance refund requirement and liability entries instead of a second Featuring
charge, placement, or inventory allocation.

Settlement is platform revenue only: featuring does not create creator or group
earnings. Unpaid reservations expire, paid bookings activate and expire through
the scheduled lifecycle worker, and the replay-safe admin reconciliation is an
operational recovery path rather than a policy bypass. Platform or moderation
ineligibility stops a placement and creates a floor-rounded compensating refund
for unused time; creator-voluntary early termination stops serving without an
automatic refund. A pre-start cancellation at or before its snapshotted cutoff
receives a full refund; a later pre-start cancellation does not.

All partial refunds share the original ledger lock and a cumulative cap across
reasons, so their total can never exceed the snapshotted booking price. A later
full provider refund or chargeback reverses only the remaining frozen amount;
chargeback is terminal and cannot be overwritten by moderation reconciliation.
Booking lifecycle commands and provider callbacks use payment-attempt-before-
booking lock ordering, including scheduled activation/expiry scans, preventing
boundary callbacks from deadlocking or reviving a cancelled placement.

Sponsored results are a separately labelled insertion after Discovery has
already generated, filtered and ranked organic candidates. A sponsored target
must be in that same eligible candidate set, is deduplicated from its organic
copy, and cannot alter any organic score. Marketplace targets must remain
published and in stock; live targets must be publicly live. Blocks, suspension,
moderation and removal override payment at booking, activation and serving time.
