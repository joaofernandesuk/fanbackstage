from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.analytics import service
from app.api.deps import CurrentIdentity, Db
from app.models.creator import CreatorProfile

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _range(starts_at: datetime | None, ends_at: datetime | None) -> tuple[datetime, datetime]:
    end = (ends_at or datetime.now(UTC)).astimezone(UTC)
    start = (starts_at or end - timedelta(days=30)).astimezone(UTC)
    if start >= end or end - start > timedelta(days=366):
        raise HTTPException(400, "Date range must be positive and at most 366 days")
    return start, end


@router.get("/creator/overview")
async def creator_analytics_overview(
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    currency: str | None = None,
) -> dict:
    creator = await db.scalar(
        select(CreatorProfile).where(CreatorProfile.user_id == identity[0].id)
    )
    if not creator:
        raise HTTPException(403, "Creator analytics are unavailable")
    if currency and len(currency) != 3:
        raise HTTPException(400, "Currency must be a three-letter code")
    start, end = _range(starts_at, ends_at)
    return {
        "creator_id": str(creator.id),
        **(await service.creator_overview(db, creator.id, start, end, currency)),
    }
