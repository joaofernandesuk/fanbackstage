# ADR-0003: Feed references and composite cursor

## Decision

Feed posts reference `ContentItem` and `MediaAsset` by foreign key. They do not duplicate content metadata, delivery URLs, ownership, access decisions, or commercial state. Feed pagination uses descending `(published_at, id)` cursors.

## Consequences

The existing access resolver and secure media delivery stay authoritative, including the subscription-versus-PPV boundary. Composite cursors are deterministic when publication timestamps tie and avoid unstable offset pagination. Discover is deliberately recency-based until a separately reviewed ranking domain exists.
