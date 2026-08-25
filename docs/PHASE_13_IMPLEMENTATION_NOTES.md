# Phase 13 Implementation Notes

The Phase 13 real-stack Playwright journey uses the production FastAPI routes,
browser authentication, Mailpit email verification, private-media processing,
development payment completion, feature lifecycle reconciliation, and seeded
moderator identities. It never mocks Trust & Safety domain services.

Credible `underage_concern` reports against media immediately create one
replay-safe temporary-containment action in the same transaction as the report
and evidence. The reporter is retained as the initiating actor in the immutable
audit trail; a subsequent moderator decision remains separately recorded.

Mandatory verified consent is a public-serving prerequisite. A pending,
revoked, or expired release prevents publication and removes the content from
public content projections, Discovery, and feature-eligibility reconciliation.
Verification of a scoped superseding release restores only that scoped content;
previous releases and moderation evidence remain append-only history.
