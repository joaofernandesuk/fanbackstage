from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.analytics import service
from app.api.deps import CurrentIdentity, Db
from app.groups import service as groups_service
from app.models.creator import CreatorProfile
from app.models.groups import Group

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


@router.get("/groups/{group_id}/creators")
async def group_creator_analytics(
    group_id: str,
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> dict:
    from uuid import UUID

    try:
        resolved = UUID(group_id)
    except ValueError as exc:
        raise HTTPException(404, "Group not found") from exc
    group = await db.get(Group, resolved)
    if not group or not await groups_service.manager_membership(db, group.id, identity[0].id):
        raise HTTPException(403, "Group analytics permission denied")
    start, end = _range(starts_at, ends_at)
    creator_ids = await service.current_managed_creators(db, group.id, identity[0].id)
    return {
        "group_id": str(group.id),
        "active_managed_creator_ids": [str(item) for item in creator_ids],
        "creator_comparison": await service.group_creator_comparison(
            db, group.id, identity[0].id, start, end
        ),
    }


@router.get("/groups/{group_id}/overview")
async def group_analytics_overview(
    group_id: str,
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    currency: str | None = None,
) -> dict:
    from uuid import UUID

    try:
        resolved = UUID(group_id)
    except ValueError as exc:
        raise HTTPException(404, "Group not found") from exc
    group = await db.get(Group, resolved)
    if not group or not await groups_service.manager_membership(db, group.id, identity[0].id):
        raise HTTPException(403, "Group analytics permission denied")
    if currency and len(currency) != 3:
        raise HTTPException(400, "Currency must be a three-letter code")
    start, end = _range(starts_at, ends_at)
    return {
        "group_id": str(group.id),
        **(await service.group_overview(db, group.id, start, end, currency)),
    }
