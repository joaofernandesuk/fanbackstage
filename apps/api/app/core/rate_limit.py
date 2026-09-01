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


async def enforce_media_rate_limit(request: Request, subject: str = "anonymous") -> None:
    """Bound upload and delivery authorization work by client and authenticated subject."""
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    key = f"fanbackstage:rate-limit:media:{_key(f'{client_ip}:{subject}')}"
    redis = Redis.from_url(settings.redis_url)
    try:
        attempts = await redis.incr(key)
        if attempts == 1:
            await redis.expire(key, settings.media_rate_limit_window_seconds)
        if attempts > settings.media_rate_limit_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many media requests. Please try again later.",
            )
    finally:
        await redis.aclose()


async def enforce_social_rate_limit(request: Request, subject: str, action: str) -> None:
    """Throttle social write commands; authorization still occurs in each domain command."""
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    key = f"fanbackstage:rate-limit:social:{action}:{_key(f'{client_ip}:{subject}')}"
    redis = Redis.from_url(settings.redis_url)
    try:
        attempts = await redis.incr(key)
        if attempts == 1:
            await redis.expire(key, settings.social_rate_limit_window_seconds)
        if attempts > settings.social_rate_limit_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many social actions. Please try again later.",
            )
    finally:
        await redis.aclose()


async def enforce_messaging_rate_limit(request: Request, subject: str, action: str) -> None:
    """Throttle spam-sensitive messaging writes independently of social actions."""
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    key = f"fanbackstage:rate-limit:messaging:{action}:{_key(f'{client_ip}:{subject}')}"
    redis = Redis.from_url(settings.redis_url)
    try:
        attempts = await redis.incr(key)
        if attempts == 1:
            await redis.expire(key, settings.messaging_rate_limit_window_seconds)
        if attempts > settings.messaging_rate_limit_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many messaging actions. Please try again later.",
            )
    finally:
        await redis.aclose()


async def enforce_streaming_rate_limit(request: Request, subject: str, action: str) -> None:
    """Streaming command throttle; durable authorization remains in the domain."""
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    key = f"fanbackstage:rate-limit:streaming:{action}:{_key(f'{client_ip}:{subject}')}"
    redis = Redis.from_url(settings.redis_url)
    try:
        attempts = await redis.incr(key)
        is_reaction = action == "live_reaction"
        window = (
            settings.streaming_reaction_rate_limit_window_seconds
            if is_reaction
            else settings.streaming_rate_limit_window_seconds
        )
        limit = (
            settings.streaming_reaction_rate_limit_attempts
            if is_reaction
            else settings.streaming_rate_limit_attempts
        )
        if attempts == 1:
            await redis.expire(key, window)
        if attempts > limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many streaming actions. Please try again later.",
            )
    finally:
        await redis.aclose()


async def enforce_discovery_rate_limit(request: Request, subject: str = "anonymous") -> None:
    """Bound broad search enumeration without changing authorization semantics."""
    settings = get_settings()
    client_ip = request.client.host if request.client else "unknown"
    key = f"fanbackstage:rate-limit:discovery:{_key(f'{client_ip}:{subject}')}"
    redis = Redis.from_url(settings.redis_url)
    try:
        attempts = await redis.incr(key)
        if attempts == 1:
            await redis.expire(key, settings.discovery_rate_limit_window_seconds)
        if attempts > settings.discovery_rate_limit_attempts:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many discovery requests. Please try again later.",
            )
    finally:
        await redis.aclose()
