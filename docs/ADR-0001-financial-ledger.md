# ADR-0001: Use an immutable balanced ledger for financial history

## Decision

Financial value movement is represented by immutable ledger transactions and debit/credit entries. Wallet balances are derived from those entries. Corrections use a linked compensating transaction.

## Consequences

The database protects posted transaction and entry history from normal updates/deletes. Each transaction must balance within one currency. This makes historical commission snapshots and refunds auditable while allowing future account categories and allocation steps.
