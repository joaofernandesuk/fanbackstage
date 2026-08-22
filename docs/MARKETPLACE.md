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


## 12.2 Creator address privacy

The purchaser must not automatically receive the creator's residential/return address. Shipping architecture must separate fulfilment/contact information from public order data and use platform-approved return/shipping solutions where possible.

## 12.3 Payment, fulfilment, reversal and earnings lifecycle

- Only a verified paid order may enter processing or be shipped. Shipment/carrier/tracking facts are append-only audit records and are never overwritten.
- Seller cancellation is allowed only before shipment. It creates an immutable refund reversal and restores the original stock once; repeated cancellation or refund commands are idempotent.
- A buyer or provider dispute blocks marketplace-earnings release immediately. A verified provider chargeback or refund creates a compensating reversal from the original order ledger transaction; it never resolves a seller's current group membership or contract.
- Buyer delivery confirmation starts the tier/hold duration snapshotted on that order. The release worker can move the exact historical pending creator/group allocations once, only after the hold and only without an unresolved dispute, refund or chargeback.
- Refunds after earnings release debit the original released allocations through compensating entries. Historical price, shipping allowance, tracking, commission, seller-tier and group-split snapshots remain immutable.

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
