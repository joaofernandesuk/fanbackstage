# Phase 13 Implementation Notes

The Phase 13 real-stack Playwright journey uses the production FastAPI routes,
browser authentication, Mailpit email verification, private-media processing,
development payment completion, feature lifecycle reconciliation, and seeded
moderator identities. It never mocks Trust & Safety domain services.

An `underage_concern` report creates immutable evidence and immediately elevates
the case to the critical, action-required urgent queue. A user report is a
safety signal, not an enforcement decision: it cannot mutate another creator's
content. Reversible containment uses the separately permissioned moderation
action path (or a separately authenticated trusted automated signal).

Mandatory verified consent is a public-serving prerequisite. A pending,
revoked, or expired release prevents publication and removes the content from
public content projections, Discovery, and feature-eligibility reconciliation.
Verification of a scoped superseding release restores only that scoped content;
previous releases and moderation evidence remain append-only history.
