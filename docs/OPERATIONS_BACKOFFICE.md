# Operations backoffice

The browser operations workspaces are projections and command surfaces over the existing domain services. They do not create parallel payment, verification, moderation, or consent truth.

## Finance

`/admin/finance` provides a paginated payment-attempt queue with account, creator, provider, source-domain, status, currency, refund-state, date-range, and exception filters. Detail responses expose normalized provider event history, audit history, refund-review state, and immutable ledger allocations, never provider payloads or credentials. Ordinary admins have read access. Refund, reconciliation, and sensitive finance-audit permissions are reserved for super-admins. The only browser refund command currently enabled is a full staging-sandbox refund; it queues the signed sandbox provider callback and relies on the existing domain-specific reversal settlement. Production-provider commands remain unavailable until an adapter supports them.

## Creator KYC

`/admin/creator-kyc` projects provider-normalized creator verification state without identity documents or raw callback payloads. A reviewer may reject, request re-verification, or leave an audited review note when the current state is `needs_review`. Manual approval is intentionally prohibited: verified identity and adulthood still require a signed provider outcome. Decisions lock and re-read the verification row, so stale state-changing decisions fail.

## Appeals

`/appeals` derives appealable enforcement actions from the signed-in account; users do not supply action identifiers. `/moderation/appeals` provides a paginated review queue, assignment, safe enforcement context, and reasoned outcomes. Eligibility, deadlines, duplicate prevention, reviewer separation, and final state remain server-enforced. Decisions append audit history and stale decisions fail.

## Consent and releases

`/creator-studio/consent` derives content and performer choices from the signed-in creator. `/moderation/consent` provides a paginated review queue, safe content context, and the matching performer identity/age-verification status. Evidence references are omitted unless the operator separately holds `moderation.sensitive_evidence`; the creator account email is not included in this review projection. Verification and rejection preserve history, require a reason, prohibit self-review, append audit history, and reject stale decisions.

All operator mutations use the existing authenticated cookie/session and CSRF-origin protections. UI link visibility is convenience only; API permissions remain authoritative.
