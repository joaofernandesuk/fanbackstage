# FanBackstage Phase 1 implementation notes

Creator profiles are attached one-to-one to the existing account identity. Public profile data is separate from legal/KYC data: this phase persists only provider references and verification outcome, never identity documents or legal address fields.

Lifecycle is explicit: `draft -> pending_verification -> pending_review -> approved`; review can reject, approved creators can be suspended or disabled, and suspended creators can be restored. Only approval grants the creator role. Every status transition and username change creates an audit event and status-history record.

The development KYC path is deterministic for local/test use and startup rejects it in production. A production provider must implement the same provider boundary before deployment.

Public creator APIs return only approved, explicitly public profiles and exclude email, legal identity, KYC provider metadata, rejection notes, and internal status history.
