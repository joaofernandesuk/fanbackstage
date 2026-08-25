# Deployment runbook

Deploy an immutable build through a staging environment first. Run migrations once as a controlled release step, then start API and Celery workers with separate worker capacity. Confirm `/ready`, worker ping, queue depth, database connectivity, storage access, payment webhook signature verification, and outbound email delivery before routing traffic.

Roll back application code independently of data migrations. Every migration must be reviewed for lock duration and reversibility; do not rewrite released migrations. PostgreSQL needs automated backups, point-in-time recovery where supported, documented retention, and scheduled restore drills. Redis is for queues/rate limits/cache only, never durable business truth.

Media workers need FFmpeg installed, bounded concurrency/timeouts, retry monitoring, and cleanup of temporary objects. LiveKit production needs TLS, TURN/STUN, bandwidth and regional capacity planning, and an explicit recording policy.
