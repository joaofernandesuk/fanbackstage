from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db
from app.creators import service as creator_service
from app.models.creator import CreatorProfile, CreatorStatus
from app.permissions.policies import Permission, authorize
from app.schemas.auth import MessageResponse

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/foundation", response_model=MessageResponse)
async def foundation(identity: CurrentIdentity) -> MessageResponse:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    return MessageResponse(message="FanBackstage admin foundation")


async def review_action(
    profile_id: str, target: CreatorStatus, identity: CurrentIdentity, db: Db
) -> MessageResponse:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    profile = await db.scalar(select(CreatorProfile).where(CreatorProfile.id == profile_id))
    if not profile:
        raise HTTPException(status_code=404, detail="Creator application not found")
    try:
        await creator_service.set_status(db, profile, target, identity[0].id)
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MessageResponse(message=f"Creator {target.value}")


@router.get("/creator-applications", response_model=list[dict])
async def creator_applications(
    identity: CurrentIdentity, db: Db, status: CreatorStatus | None = None
):
    authorize(identity[0], Permission.ADMIN_ACCESS)
    query = select(CreatorProfile)
    if status:
        query = query.where(CreatorProfile.status == status)
    rows = (await db.scalars(query.order_by(CreatorProfile.created_at))).all()
    return [
        {
            "id": str(row.id),
            "username": row.username,
            "display_name": row.display_name,
            "status": row.status.value,
        }
        for row in rows
    ]


@router.post("/creator-applications/{profile_id}/approve", response_model=MessageResponse)
async def approve_creator(profile_id: str, identity: CurrentIdentity, db: Db) -> MessageResponse:
    return await review_action(profile_id, CreatorStatus.approved, identity, db)


@router.post("/creator-applications/{profile_id}/reject", response_model=MessageResponse)
async def reject_creator(profile_id: str, identity: CurrentIdentity, db: Db) -> MessageResponse:
    return await review_action(profile_id, CreatorStatus.rejected, identity, db)


@router.post("/creator-applications/{profile_id}/suspend", response_model=MessageResponse)
async def suspend_creator(profile_id: str, identity: CurrentIdentity, db: Db) -> MessageResponse:
    return await review_action(profile_id, CreatorStatus.suspended, identity, db)
