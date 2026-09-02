# Notifications and Email

Phase 15 centralizes communication delivery without taking ownership of product, financial,
entitlement, attribution, or moderation decisions. Product domains create one durable
`NotificationIntent` per recipient and source event; the notification worker owns only
channel delivery state.

`EMAIL` and `IN_APP` are the supported channels. Notification types are explicitly
classified server-side as transactional or marketing. Account security, verification,
password reset, receipts, refunds, and Trust & Safety decisions are mandatory
transactional messages. Marketing is disabled until the recipient records explicit
marketing consent.

Delivery revalidates current account state, marketing consent, and suppression state at
send time. A marketing unsubscribe therefore suppresses a queued marketing intent, but
never password-reset or other required transactional mail. Hard bounces and complaints
create durable suppression records. Delivery attempts retain a destination snapshot and
safe status/error metadata, never rendered body content or tokens.

The SMTP adapter supports local unauthenticated Mailpit only in development/test. Staging and
production startup require a non-local SMTP host, username/password authentication, and exactly
one encrypted transport mode: implicit TLS or forced STARTTLS. Private staging must use a sink
that cannot deliver to the public Internet; Mailpit is not required. The adapter remains behind
the `EmailProvider` boundary;
future provider adapters can provide independent transactional and marketing streams without
changing domain services. Provider events require the configured webhook secret and are
idempotent. SMTP messages use intent keys as stable message references.

Templates use trusted, server-built text values. In-app title/body values are escaped and
targets are restricted to canonical internal paths; arbitrary redirect URLs are rejected.
Security-token links are encrypted at rest in the notification intent using the configured
server secret and are never sent to audit metadata or logs.

Notification-center reads are recipient-scoped. Preference updates, consent changes, and
unsubscribe operations are authenticated and audited. There is intentionally no open/click
tracking, creator bulk campaign feature, SMS, or push delivery in Phase 15.
