from __future__ import annotations

from datetime import UTC, datetime

from redis.asyncio import Redis

WORKER_HEARTBEAT_KEY = "fanbackstage:operations:worker:last_seen"
BEAT_HEARTBEAT_KEY = "fanbackstage:operations:beat:last_seen"
HEARTBEAT_TTL_SECONDS = 120
CELERY_QUEUES = (
    "default",
    "media",
    "notifications",
    "notifications_marketing",
    "moderation",
    "analytics",
    "financial",
    "scheduled",
)


def _timestamp() -> int:
    return int(datetime.now(UTC).timestamp())


async def record_operational_heartbeat(redis: Redis) -> dict[str, object]:
    """Prove one beat dispatch reached a worker and expose safe queue depths."""

    observed_at = _timestamp()
    pipeline = redis.pipeline()
    pipeline.set(WORKER_HEARTBEAT_KEY, observed_at, ex=HEARTBEAT_TTL_SECONDS)
    pipeline.set(BEAT_HEARTBEAT_KEY, observed_at, ex=HEARTBEAT_TTL_SECONDS)
    for queue in CELERY_QUEUES:
        pipeline.llen(queue)
    results = await pipeline.execute()
    return {
        "observed_at": observed_at,
        "queue_depths": {
            queue: int(results[index + 2]) for index, queue in enumerate(CELERY_QUEUES)
        },
    }


async def operational_heartbeat_ready(redis: Redis, *, maximum_age_seconds: int = 90) -> bool:
    values = await redis.mget(WORKER_HEARTBEAT_KEY, BEAT_HEARTBEAT_KEY)
    if any(value is None for value in values):
        return False
    now = _timestamp()
    try:
        return all(now - int(value) <= maximum_age_seconds for value in values)
    except (TypeError, ValueError):
        return False
