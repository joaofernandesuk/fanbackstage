# Phase 3 implementation notes

Phase 3 adds the financial core for one-time PPV content purchases. It deliberately excludes subscriptions, stored-value fan wallets, group splits, payouts, tips, and other Phase 4+ payment products.

## Ledger and wallet projection

`ledger_transactions` represent immutable business events and `ledger_entries` represent their postings. Each transaction balances: debit minor units equal credit minor units, per currency. Platform clearing is debited for a successful PPV charge; platform revenue and creator pending earnings are credited. Creator wallet values are projections from ledger entries, never mutable balance columns.

The database rejects updates and deletes to posted transaction and entry rows. Refunds create a linked compensating transaction; no historical entry is altered.

## Money, commission, and allocation

All money uses integer minor units and a separate three-letter currency. Commission is an integer basis-point rate. Platform fee uses deterministic floor division (`gross * bps // 10000`); the residual minor unit remains with creator-side revenue so allocations always equal gross. Purchases snapshot gross, platform fee, creator amount, currency, and commission rate.

Phase 3 development uses no provider-fee or tax allocation. The intended future order is gross payment, known provider fees, platform commission, distributable creator revenue, then any accepted group split.

## Payment orchestration

The development payment provider emits signed payment events and is prohibited in production. Purchase initiation derives price, seller, currency, and commission exclusively from approved published PPV content. A verified success event atomically marks the attempt succeeded, posts the ledger, and issues the existing content entitlement. Database uniqueness plus row locking makes callbacks and reconciliation replay-safe.

The financial worker reconciles succeeded attempts that remain awaiting settlement. It invokes the same settlement service and therefore cannot create duplicate postings or entitlements.

## Refunds, chargebacks, and release

A full refund records a compensating ledger transaction, preserves the original purchase, and revokes its entitlement. Payment and purchase state enums retain dispute/chargeback states for later provider event handling; Phase 3 does not implement a dispute-management workflow.

Creator revenue starts in `creator_pending`. A ledger transfer moves it to `creator_available`; configured settlement windows conservatively prevent release while a newer paid purchase remains in its settlement period. Payout execution is intentionally out of scope.

## Migration policy

Migration `20260820_0004` is forward from Phase 2 and creates the financial tables, constraints, indexes, and immutability trigger. Its downgrade is destructive and only appropriate for empty local/test financial data. Production corrections must use a forward migration.

## Known limitations

The development provider is intentionally zero-fee and does not store payment instruments. There is no real processor adapter, tax engine, partial refund workflow, or chargeback operations dashboard in Phase 3.
