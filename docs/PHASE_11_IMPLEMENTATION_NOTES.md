# Phase 11 implementation notes

Discovery is a centralized read boundary with explicit result entity types: creator, post, video, gallery, marketplace listing, and live room. Candidate rows are filtered against current source-domain policy before deterministic organic scoring. No Phase 10 affiliate or referral input, financial earning value, group membership, or paid placement is included.

Cursor payloads are HMAC-signed and bound to a ranking configuration version. Configuration edits append a new version and are audit logged; manual hides are server-authorized operational exclusions. The initial trending/recommendation foundation uses bounded public recency, live state, and engagement signals with request-level event deduplication. It intentionally does not persist raw search history or precompute user recommendation tables.

Migration `20260823_0024_discovery_foundation` creates the configuration/hide/event rows and PostgreSQL public-text GIN indexes. Its downgrade removes only Phase 11 derived controls/indexes; source domain data remains untouched. Phase 12 may insert clearly distinct featured results later, but contains no implementation here.
