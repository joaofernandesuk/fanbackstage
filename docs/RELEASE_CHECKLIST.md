# Release checklist

- [ ] Review migration SQL, lock risk, rollback, and backup/restore readiness.
- [ ] Run API tests, lint/format checks, web lint/build, Compose validation, and `make smoke`.
- [ ] Verify production config validation with non-secret representative values.
- [ ] Verify CORS origins, HTTPS/secure cookies, CSP/headers, session expiry, rate limits, and webhook signatures.
- [ ] Verify worker health, queue retries, FFmpeg/media failures, private storage boundaries, and signed URLs.
- [ ] Verify payment reconciliation/refund/chargeback procedures and notification/bounce handling.
- [ ] Complete staging smoke and rollback rehearsal; obtain release approval.
