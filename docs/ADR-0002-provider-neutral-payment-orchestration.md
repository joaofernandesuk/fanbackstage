# ADR-0002: Keep payment orchestration provider-neutral

## Decision

Domain purchase settlement consumes verified provider events rather than client-reported payment success. Phase 3 supplies a signed development provider and blocks it in production.

## Consequences

Real provider adapters can be added without changing purchase, ledger, or entitlement rules. Webhook replay and failed internal settlement are handled by idempotent database-backed processing and reconciliation.
