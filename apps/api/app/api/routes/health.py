from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import engine


class HealthResponse(BaseModel):
    message: str
    service: str = "fanbackstage-api"

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(message="ok")


@router.get("/ready", response_model=HealthResponse)
async def ready() -> HealthResponse:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        redis = Redis.from_url(get_settings().redis_url)
        await redis.ping()
        await redis.aclose()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Dependencies unavailable") from exc
    return HealthResponse(message="ready")
