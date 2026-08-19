from fastapi import APIRouter, HTTPException
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import engine
from app.schemas.auth import MessageResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=MessageResponse)
async def health() -> MessageResponse:
    return MessageResponse(message="ok")


@router.get("/ready", response_model=MessageResponse)
async def ready() -> MessageResponse:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        redis = Redis.from_url(get_settings().redis_url)
        await redis.ping()
        await redis.aclose()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Dependencies unavailable") from exc
    return MessageResponse(message="ready")
