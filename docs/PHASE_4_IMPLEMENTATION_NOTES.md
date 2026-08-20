# Phase 4 implementation notes

Phase 4 reuses Phase 3 `PaymentAttempt`, signed webhook, immutable ledger, commission and creator-pending-account foundations. A subscription period is a distinct historical commercial snapshot; it records the selected duration, base price, applied promotion reference and eligibility, discount basis points/amount, charged amount, commission, platform fee, creator allocation, payment attempt, ledger transaction and creator-scoped entitlement.

Creators configure one currency-aware plan with any enabled subset of the stable 1/3/6/12-month duration enum. Promotions use a parent plus duration-rule model, allowing one promotion to use the same discount across all plans, target one/subsets, or apply different discounts per duration. The server resolves eligibility and chooses one valid promotion: the greatest discount wins, with promotion UUID as a deterministic tie-breaker; percentage promotions never stack.

New-subscriber eligibility is based on subscription history; reactivation requires prior history without an active subscription. Promotions are UTC scheduled. New-subscriber promotions are initial-only unless their renewal scope explicitly permits renewal. Plan/promotion edits affect only future periods; renewals use the current enabled price and eligible promotion. Disabling a duration prevents future renewal rather than silently migrating a subscriber.

Subscriptions issue a creator-scoped existing entitlement instead of one entitlement per content item. It authorizes published `subscription` content only, never PPV. Cancelling turns off auto-renew while retaining access through the paid end date; reactivation restores auto-renew without creating an overlap. Renewal workers create a single pending next period and the same payment webhook settles it. Failed due renewal enters configurable grace, which preserves access temporarily; finalization expires access after grace.

Production scheduling must run the `process_subscription_renewals` and `finalize_subscription_expirations` Celery tasks on the scheduled queue. The development provider remains prohibited in production.

The public subscription UI always reads the server-resolved effective price; it never sends a client-calculated amount. The real-stack Playwright journey configures all four products, creates a duration-specific promotion, completes the existing payment/webhook/ledger settlement, verifies the resulting creator-scoped entitlement can access subscription content but not PPV content, then verifies cancel/reactivate behavior. It also covers the browser's private MinIO upload path and ensures public cards expose only preview derivatives.

Migration `20260820_0006_subscriptions_promotions` is exercised in CI from the Phase 3 head (`20260820_0005`) to Phase 4 head. Its downgrade is intentionally only for empty local/test databases because it removes subscription history; deployed rollback must be a forward corrective migration.
