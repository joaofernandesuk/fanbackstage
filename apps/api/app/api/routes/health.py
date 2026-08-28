from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text

from app.compliance.policy import production_policy_readiness
from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.legal.service import production_legal_readiness


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
        async with SessionLocal() as session:
            compliance_ready, _ = await production_policy_readiness(session)
            legal_ready, _ = await production_legal_readiness(session)
            if not compliance_ready or not legal_ready:
                raise RuntimeError("Production compliance or legal policy is not ready")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Dependencies unavailable") from exc
    return HealthResponse(message="ready")
