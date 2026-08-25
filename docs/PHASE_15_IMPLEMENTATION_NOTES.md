# Phase 15 Implementation Notes

Phase 15 introduces a provider-neutral notifications module and forward migration
`20260826_0032`. Its `notification_intents` table is the exactly-once business-intent
boundary; Celery transport remains at-least-once and checks existing successful delivery
attempts before dispatching another email.

Indexes follow the implemented query shapes: recipient plus `created_at` and recipient plus
`read_at` power notification center and unread count; intent/status indexes power delivery
and reconciliation; provider message ID supports webhook lookup; email hash supports
suppression lookup. These indexes affect no business semantics.

Authentication verification and password reset now create durable transactional intents after
their existing opaque one-time tokens are issued. The notification worker sends through the
same local Mailpit SMTP path, preserving verification/reset token lifecycle and rate limits.
