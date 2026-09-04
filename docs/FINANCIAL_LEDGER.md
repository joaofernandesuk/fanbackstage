# Financial Ledger, Commissions and Payouts

Phase 6 message unlocks and paid sends are `messaging_charge` ledger transactions. They snapshot gross, platform fee, creator amount, currency, and commission basis points before payment; webhook replay uses the same payment-attempt and ledger idempotency guarantees as PPV. A paid send is not delivered until its verified charge settles.

Phase 7 private sessions use the `private_live_session` transaction type. The request snapshots the per-minute rate, minimum charge, currency, authorization cap, participant mode and commission before acceptance. Server-persisted billable seconds—not browser timers—produce one deterministic final charge: `ceil(rate_minor * billable_seconds / 60)`, bounded by the authorization cap and raised to the configured minimum where applicable. Settlement and refunds use the existing immutable-ledger compensating-entry path; never mutable session balances.

Phase 7 public Live tips, gifts, and accepted paid requests settle through the `live_commerce` transaction type. A paid request snapshots its server-owned option price and acceptance requirement. Verified capture is necessary but does not settle an acceptance-required request; creator acceptance creates exactly one ledger transaction. Decline, expiry, or late success after expiry records a `live_paid_request` excess-capture refund requirement and liability transaction. Supporter rankings and goals read eligible canonical Live events linked to these immutable transactions and exclude reversed/refunded value.

Paid private peeks use the `private_live_peek` transaction type. The accepted `PrivateSession` freezes the admin-owned price, currency and commission before any viewer can purchase. A peek settles once only while that exact session remains deliverable; late confirmation after decline, expiry or termination uses the `private_live_peek` excess-capture reversal path. The browser cannot supply price, commission, session identity or financial success.

Financial source-of-truth rules. Any feature creating economic value must integrate here rather than maintaining its own balance.

# 22. Unified Wallet and Immutable Financial Ledger

Do not treat a mutable balance column as the source of truth. Balances are derived/cached representations of immutable ledger entries.

```text
LedgerEntry / FinancialTransaction
transaction_id
source_type / source_id
currency
gross_amount
platform_fee
processor_fee (where represented)
creator_pool
group_share
creator_share
referral_share
tax / withholding fields where applicable
status
created_at
settled_at
reversal_of_transaction_id
```


## 21.1 Example settlement

```text
Video purchase: €20
Platform commission: 20% = €4
Net creator-side pool: €16
Active agency contract: 50/50
Creator: €8
Agency: €8
```

The exact commission configuration and contract version used must be referenced by the transaction. Later changes do not affect the result.


## 21.2 Ledger principles

- Double-entry accounting is preferred for financial correctness.
- Never delete financial history; corrections are reversals/adjustments.
- Idempotency keys for all payment callbacks and purchase commands.
- Pending, available, reserved, paid-out and reversed states are distinct.
- Chargebacks/refunds create explicit financial consequences.
- Payouts must be reconcilable to underlying earnings.
- Multi-currency policy must be explicit; never silently mix balances.

# 23. Platform Commission Engine

Commission must be configurable in backoffice and support controlled overrides with deterministic precedence.

- Platform default commission.
- Creator-specific override.
- Group/agency override where business policy allows.
- Revenue-type override: webcam, PPV, marketplace, subscription, tips, messaging, etc.
- Time-bound promotional override.
- Featuring usually represents platform revenue rather than creator revenue split, but this must be encoded explicitly.

A commission resolver should return the effective rule plus the rule/version IDs used, allowing every transaction to explain why it settled as it did.

# 24. Financial Dashboards


## 23.1 Platform admin dashboard

- GMV
- Platform net/gross revenue
- Creator revenue
- Agency revenue
- Pending/available payouts
- Completed payouts
- Refunds
- Chargebacks
- Subscription revenue
- PPV revenue
- Webcam/private revenue
- Messaging revenue
- Marketplace revenue
- Tips
- Featuring/promotion revenue
- Referral/affiliate cost
- Processor fees where known

Filters: today, yesterday, 7 days, 30 days, current month, prior month and custom range; further filter by creator, agency, country/region where lawful, revenue type and currency.


## 23.2 Creator dashboard

- Revenue by source
- Gross versus net
- Pending and available balance
- Payout history
- Profile visits
- Followers/subscribers
- Conversion rate
- Subscription churn/renewal
- ARPU/ARPPU where meaningful
- Top content
- Top customers subject to privacy policy
- Webcam hours and revenue/hour
- Promotion spend and attributable performance


## 23.3 Group dashboard

- Managed creators
- Revenue by creator
- Revenue split by contract version
- Manager activity
- Creator growth/performance
- Group earnings/payout state
- Permission and contract status

# 33. Payment and Payout Abstraction

Do not tightly couple domain logic to a single payment processor. Implement provider adapters around payment intents/charges, refunds, disputes, identity/payout onboarding and webhooks. Adult-industry acceptance must be validated with the selected provider before production.

- Idempotent webhooks
- Signature verification
- Event replay protection
- Provider reference IDs
- Explicit payment state machine
- Payout state machine
- Refund and dispute workflows
- Processor errors isolated from entitlement state until payment certainty exists

# Phase 3 implementation notes

Phase 3 records PPV purchases in integer minor units with an explicit ISO currency. A purchase snapshots its commission basis points, platform fee, and creator amount before payment completion; later commission changes cannot alter that history.

The development payment adapter is available only outside production. It signs development webhook payloads with `FANBACKSTAGE_PAYMENT_WEBHOOK_SECRET`; payment completion, webhook replay protection, ledger posting, and entitlement issuance all use the same webhook-processing path. Production startup rejects the development adapter.

Payment recovery is replay-safe: the financial worker reconciles `succeeded` payment attempts whose purchase is still awaiting settlement. It uses the same idempotent settlement service as webhooks, so reconciliation cannot create a second ledger transaction or entitlement. Creator release is blocked while the configured settlement window contains newer paid purchases.

## Payment retry, dispute and reversal invariants

Buyer payment commands serialize on the buyer plus `Idempotency-Key`, and signed
provider callbacks serialize on provider plus external event ID. A transport
retry reuses its key and canonical attempt; a deliberate retry after a confirmed
failure uses a new key while retaining immutable attempt history for the same
purchase, subscription period or featuring booking.

The first valid capture may settle value once. A later successful capture for a
stale attempt creates a balanced `excess_capture_liability` and a durable
`PaymentRefundRequirement`; it never creates a second entitlement, order,
subscription period, message, private session or featuring placement. Provider
refund completion posts one compensating refund/chargeback transaction against
that liability. Production still requires a real payment/refund adapter; the
development provider and its synthetic references are local/test-only.

A signed dispute is fail-closed but is not itself a refund. Protected access is
revoked or suspended and the attempt/domain enters a disputed state. If creator
or referral earnings were already released, `payment_dispute_hold` moves the
same frozen allocation from available back to pending without changing revenue.
Final refund or chargeback reverses the exact original frozen ledger allocation
once. Chargeback dominates an earlier refund without a second reversal; a later
refund cannot downgrade chargeback. Earnings releases, dispute holds and excess
capture containment are internal balance/operations movements and are excluded
from GMV and ordinary revenue/refund analytics.

# Phase 12 featuring ledger notes

Successful featuring payment posts one immutable balanced `featuring_charge`
transaction from platform clearing to platform revenue using the booking's
snapshotted amount and currency. It is not creator or group income and does not
participate in payout calculations. Duplicate payment provider events settle the
same booking and ledger transaction at most once. Refunds and chargebacks are
compensating immutable transactions: a chargeback reverses the exact original
featuring charge once and deactivates the placement; repeated webhooks and
reconciliation remain harmless.

`ledger_transactions` and `ledger_entries` are append-only at the database layer. Refunds create reversal entries and revoke the purchase entitlement; they never edit or delete the original purchase entries. Migration `20260820_0004` has a destructive downgrade for local development only. A deployed rollback must use a forward corrective migration.

# Phase 4 implementation notes

Subscription charges use the same Phase 3 payment attempt, verified webhook and balanced ledger path. Every `SubscriptionPeriod` snapshots its commercial inputs and outputs, so later plan, promotion or commission changes cannot rewrite charged history. Subscription revenue credits the existing platform revenue and creator pending accounts; no subscription-specific mutable balance is introduced.
