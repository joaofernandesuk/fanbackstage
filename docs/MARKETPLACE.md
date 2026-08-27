# Creator Marketplace

Creator storefronts, physical/digital fulfilment, custom content requests, privacy and goal/wishlist commerce.

# 12. Marketplace

Each creator can operate a storefront for permitted physical and digital goods.

- Clothing and lingerie
- Signed items and memorabilia
- Polaroids/photos
- Custom physical items
- Digital items
- Custom content products
- Other platform-approved creator merchandise


## 12.1 Product model

- Title, description, photos, price, stock, condition, variants/SKUs, shipping regions, shipping charge, status, moderation state.
- Order states, cancellation rules, shipment state and dispute/refund state.
- Physical and digital fulfilment are distinct flows.
- Public listing photos use creator-owned, approved `safe_public` display derivatives dedicated to marketplace presentation. An asset already attached to content, a Story, a feed post, or a message cannot be reused as listing media; attachment, public projection, and derivative delivery all enforce this boundary so a listing cannot bypass another domain's access policy.
- Public listing responses project only server-issued derivative delivery paths plus the eligible seller's public identity. Original storage keys and demo/persona fallback images are never marketplace business data.


## 12.2 Creator address privacy

The purchaser must not automatically receive the creator's residential/return address. Shipping architecture must separate fulfilment/contact information from public order data and use platform-approved return/shipping solutions where possible.

## 12.3 Payment, fulfilment, reversal and earnings lifecycle

- Checkout serializes by buyer and `Idempotency-Key`. Ambiguous transport retries reuse the key; a canonical terminal payment response requires a new key and a fresh stock reservation. Reservation expiry, admin reversal, and signed provider callbacks use payment-attempt-before-order lock ordering, so a boundary success deterministically either settles the live reservation or creates an excess-capture refund liability after stock restoration.
- Only a verified paid order may enter processing or be shipped. Shipment/carrier/tracking facts are append-only audit records and are never overwritten.
- Seller cancellation is allowed only before shipment. It creates an immutable refund reversal and restores the original stock once; repeated cancellation or refund commands are idempotent.
- A buyer or provider dispute blocks marketplace-earnings release immediately. A verified provider chargeback or refund creates a compensating reversal from the original order ledger transaction; it never resolves a seller's current group membership or contract.
- Buyer delivery confirmation starts the tier/hold duration snapshotted on that order. The release worker can move the exact historical pending creator/group allocations once, only after the hold and only without an unresolved dispute, refund or chargeback.
- Refunds after earnings release debit the original released allocations through compensating entries. Historical price, shipping allowance, tracking, commission, seller-tier and group-split snapshots remain immutable.
- `earnings_released_at` is immutable provenance. A later dispute holds the correct released allocation without making it releasable again; seller-favour resolution restores `released`, while refund/chargeback reverses the frozen original once. Chargeback may dominate an earlier refund without posting a second reversal.
- Buyer order history exposes only buyer-safe order and tracking information. Creator fulfilment controls use the same paid-order state machine and show the pending marketplace hold; neither surface computes commission, split, allowance or release eligibility in the client.
- Creator and group earnings views include marketplace as a ledger-derived revenue source. Reversal entries are attributed to the original marketplace source so a refund or chargeback cannot be hidden by current seller or group settings.
- Marketplace listings can be reported for prohibited items. Reports are deduplicated per reporter/reason/listing; moderators can remove a reported listing through an audited action. Marketplace suspension is platform-admin controlled and blocks new listings and checkout of a suspended seller's catalogue without rewriting historical orders.
- Delegated managers require the separate, current `manage_marketplace_orders` grant for a specific active creator membership to inspect or perform fulfilment actions. The server retains the manager as the audit actor and revocation applies to the next request.

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
