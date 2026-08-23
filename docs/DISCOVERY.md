# Discovery, Search and Organic Ranking

Phase 11 provides a centralized, PostgreSQL-first discovery read domain. It is deliberately a projection layer: creator approval, content access, moderation, marketplace availability, live state, follows, and blocks remain owned by their existing domains.

## Pipeline

`candidate generation -> hard policy filtering -> deterministic organic ranking -> signed cursor pagination`.

All public endpoints return dedicated result envelopes, never ORM records. Locked content can expose only safe metadata, its lock state, and an authorised derivative identifier. It never exposes a source URL, storage key, entitlement state, or private-message attachment.

Anonymous discovery is permitted for safe public objects. Sold-out listings remain visible as `sold_out` but their existing checkout command remains authoritative. User-specific discovery applies bidirectional blocks before rank; no cached personalized result is shared between users.

## Ranking and recommendations

The current versioned configuration has bounded text, live, recency, and engagement weights plus a bounded trending window and result size. Stable ordering is score, timestamp, and public UUID. Config updates create a new row/version and are audited. Search/click/impression events record the ranking version with request-level deduplication; they are analytics only and cannot affect ledger, referrals, or access.

Cold-start recommendations are the same globally eligible recent/live organic candidates. No private messages, KYC, shipping, payment-card information, private earnings, or affiliate performance participates in ranking. Phase 12 may add a distinct labelled insertion layer for paid featuring; Phase 11 contains no sponsored score, inventory, or booking logic.

## Performance and limits

The migration adds PostgreSQL GIN text indexes for creator and marketplace public text. Results are bounded to 50, broad text queries require two normalized characters, cursors are HMAC-signed and configuration-version-bound, and the discovery rate limiter protects public enumeration. The initial implementation uses bounded candidate sets and avoids a separate search cluster; a rebuildable projection table or OpenSearch requires a later measured performance decision.
