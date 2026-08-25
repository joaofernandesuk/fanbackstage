# Incident response

1. Declare severity, preserve timestamps/correlation IDs, and assign an incident owner.
2. Contain: disable compromised credentials/webhooks, revoke sessions or media access where appropriate, and pause affected workers or payment actions.
3. Investigate from structured API logs, audit events, payment/provider events, and immutable ledger records; do not alter evidence.
4. Recover with reviewed changes, reconciliation, and least-privilege credential rotation.
5. Record customer impact, decisions, follow-up owners, and a restore/security test.

For suspected media exposure, first revoke entitlement/access paths and signed URL issuance, preserve audit records, then notify the security owner. For payment incidents, stop retries only after preserving provider event IDs and reconcile every affected ledger transaction.
