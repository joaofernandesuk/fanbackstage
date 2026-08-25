# Production environment requirements

No production environment is configured by this repository. Production startup rejects development KYC/payment providers, demo seeding, default session/notification/LiveKit/storage secrets, insecure cookies, local Mailpit, and local MinIO.

Provision unique secrets for PostgreSQL, Redis, object storage, sessions, payment webhooks, notification webhooks, LiveKit, and any encryption keys. Use HTTPS origins, secure cookies, a transactional provider with SPF/DKIM/DMARC and bounce handling, separate production payment credentials, private object storage with CDN/signed-URL policy, and managed Redis/PostgreSQL credentials.

For `FanBackstage.com`, decide canonical web host, API host, media/CDN host, transactional and marketing mail subdomains before DNS/TLS work. Do not point DNS until the deployment checklist is approved.
