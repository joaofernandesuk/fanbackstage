import hashlib

from fastapi import HTTPException, Request, status
from redis.asyncio import Redis

from app.core.config import get_settings


def _key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


async def enforce_auth_rate_limit(request: Request, subject: str = "anonymous") -> None:
    """Redis-backed throttle for sensitive account commands; never durable product state."""
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    key = f"fanbackstage:rate-limit:auth:{_key(f'{client_ip}:{subject}')}"
    redis = Redis.from_url(settings.redis_url)
    try:
        attempts = await redis.incr(key)
        if attempts == 1:
            await redis.expire(key, settings.auth_rate_limit_window_seconds)
        if attempts > settings.auth_rate_limit_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many attempts. Please try again later.",
            )
    finally:
        await redis.aclose()
