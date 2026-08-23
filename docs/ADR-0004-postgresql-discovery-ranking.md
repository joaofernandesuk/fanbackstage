# ADR-0004: PostgreSQL-first deterministic organic discovery

## Decision

Discovery uses PostgreSQL source-domain joins and GIN text indexes initially. Candidate generation is separated from policy filtering, deterministic organic ranking, and signed cursor pagination. Ranking configuration is a bounded, versioned administrative model; it has no executable formulas and no paid-placement input.

## Consequences

Serving-time policy filters remain authoritative even if a future derived index is introduced. The system can explain a ranking version without retaining private raw search histories. Future sponsored placement must be a separate Phase 12 insertion layer and cannot masquerade as organic rank.
