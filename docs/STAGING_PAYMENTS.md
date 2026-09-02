# Staging payment sandbox

`staging_sandbox` is a fictional, provider-shaped payment adapter available only in `staging` and `test`. No commercial provider is selected in this repository and this adapter must never be interpreted as a production processor integration.

## Flow

1. A normal domain command creates the existing immutable `PaymentAttempt` and domain reservation.
2. The browser requests its checkout action. In staging it submits one explicit fictional outcome (`SUCCESS`, `DECLINE`, `DELAYED_SUCCESS`, `REFUND`, `DISPUTE`, or `CHARGEBACK`) to the staging checkout boundary.
3. That boundary records a durable sandbox delivery event; it cannot settle a purchase.
4. A worker emits a HMAC-SHA256 signed provider-style callback. The normal payment webhook processor verifies it, deduplicates `(provider, event_id)`, and performs the existing ledger/entitlement/reversal transition.
5. Reconciliation remains the recovery mechanism for a verified success whose first settlement transaction stopped part-way through.

The sandbox uses opaque references only; it accepts no cardholder data or production endpoint. It is rejected in production configuration. Missing secret or environment marker keeps staging readiness red.

## Operations

`GET /admin/compliance/creator-kyc` is unrelated to payment. Payment operations continue to use the existing authorised finance views, `payment_attempts`, `payment_webhook_events`, and immutable ledger records. Sandbox event records contain only an attempt, fake external event ID, event type and delivery time.

## Future provider contract

A real adapter must implement checkout intent/reference creation, signed callback verification, safe state retrieval/reconciliation, refund/cancel/dispute mappings and idempotency. It must use the same `PaymentAttempt` and webhook settlement service; it must not create a parallel ledger or frontend completion path.
