from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db
from app.content import service as content_service
from app.creators import service as creator_service
from app.models.content import ContentItem, ContentStatus
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


async def content_review_action(
    content_id: str, action: str, identity: CurrentIdentity, db: Db
) -> MessageResponse:
    authorize(identity[0], Permission.MODERATION_ACCESS)
    content = await db.get(ContentItem, content_id)
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")
    try:
        if action == "approve":
            await content_service.approve(db, content, identity[0])
        else:
            await content_service.reject(db, content, identity[0])
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MessageResponse(message=f"Content {action}d")


@router.get("/content-review", response_model=list[dict])
async def content_review_queue(identity: CurrentIdentity, db: Db) -> list[dict]:
    authorize(identity[0], Permission.MODERATION_ACCESS)
    rows = (
        await db.scalars(
            select(ContentItem)
            .where(ContentItem.status == ContentStatus.pending_review)
            .order_by(ContentItem.created_at)
        )
    ).all()
    return [
        {"id": str(row.id), "title": row.title, "content_type": row.content_type.value}
        for row in rows
    ]


@router.post("/content-review/{content_id}/approve", response_model=MessageResponse)
async def approve_content(content_id: str, identity: CurrentIdentity, db: Db) -> MessageResponse:
    return await content_review_action(content_id, "approve", identity, db)


@router.post("/content-review/{content_id}/reject", response_model=MessageResponse)
async def reject_content(content_id: str, identity: CurrentIdentity, db: Db) -> MessageResponse:
    return await content_review_action(content_id, "reject", identity, db)
