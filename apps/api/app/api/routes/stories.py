from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db, OptionalIdentity
from app.core.config import get_settings
from app.core.rate_limit import (
    enforce_discovery_rate_limit,
    enforce_media_rate_limit,
    enforce_social_rate_limit,
)
from app.media.storage import storage_provider
from app.models.content import MediaAsset, MediaStatus
from app.models.creator import CreatorProfile, CreatorVerification, VerificationStatus
from app.models.story import Story, StoryStatus
from app.schemas.story import (
    StoryCreate,
    StoryCreatorResponse,
    StoryMediaResponse,
    StoryRailResponse,
    StoryResponse,
)
from app.stories import service

router = APIRouter(prefix="/stories", tags=["stories"])


async def story_response(db: Db, story: Story) -> StoryResponse:
    creator = await db.get(CreatorProfile, story.creator_id)
    asset = await db.get(MediaAsset, story.media_asset_id)
    if (
        not creator
        or not creator.username
        or not asset
        or asset.owner_creator_id != story.creator_id
    ):
        raise HTTPException(status_code=404, detail="Story not found")
    derivative = await service.delivery_derivative(db, asset)
    if not derivative:
        raise HTTPException(status_code=404, detail="Story media not found")
    verification = await db.scalar(
        select(CreatorVerification)
        .where(CreatorVerification.creator_profile_id == creator.id)
        .order_by(CreatorVerification.created_at.desc())
        .limit(1)
    )
    return StoryResponse(
        id=story.id,
        status=story.status.value,
        creator=StoryCreatorResponse(
            id=creator.id,
            username=creator.username,
            display_name=creator.display_name or creator.username,
            avatar_reference=creator.avatar_reference,
            verified=bool(
                verification
                and verification.status is VerificationStatus.verified
                and verification.adult_verified
            ),
        ),
        media_type=asset.media_type.value,
        caption=story.caption,
        alt_text=story.alt_text,
        access_policy=story.access_policy.value,
        created_at=story.created_at,
        published_at=story.published_at,
        expires_at=story.expires_at,
        media=StoryMediaResponse(
            derivative_id=derivative.id,
            mime_type=derivative.mime_type,
            delivery_path=f"/stories/{story.id}/media",
        ),
    )


@router.post("", response_model=StoryResponse, status_code=201)
async def create_story(
    payload: StoryCreate,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> StoryResponse:
    try:
        await enforce_social_rate_limit(request, str(identity[0].id), "story_create")
        story = await service.create_story(
            db,
            identity[0],
            payload.media_asset_id,
            payload.caption,
            payload.alt_text,
            payload.access_policy,
            idempotency_key or "",
        )
        await db.commit()
        return await story_response(db, story)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400,
            detail=str(exc),
        ) from exc


@router.get("/mine", response_model=list[StoryResponse])
async def own_stories(
    identity: CurrentIdentity,
    db: Db,
    status: StoryStatus | None = None,
    limit: int = Query(default=100, ge=1, le=100),
) -> list[StoryResponse]:
    try:
        await service.expire_due_stories(db)
        rows = await service.own_stories(db, identity[0], status, limit)
        await db.commit()
        return [await story_response(db, story) for story in rows]
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/rail", response_model=StoryRailResponse)
async def public_rail(
    request: Request,
    identity: OptionalIdentity,
    db: Db,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=50),
    creator_username: str | None = Query(
        default=None, min_length=3, max_length=32, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]+$"
    ),
) -> StoryRailResponse:
    try:
        await enforce_discovery_rate_limit(
            request, str(identity[0].id) if identity else "anonymous"
        )
        rows, next_cursor = await service.public_rail(
            db,
            identity[0] if identity else None,
            cursor,
            limit,
            creator_username,
        )
        return StoryRailResponse(
            items=[await story_response(db, story) for story in rows],
            next_cursor=next_cursor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{story_id}/media")
async def story_media(
    story_id: UUID,
    request: Request,
    identity: OptionalIdentity,
    db: Db,
) -> RedirectResponse:
    await enforce_media_rate_limit(request, str(identity[0].id) if identity else "anonymous")
    story = await db.get(Story, story_id)
    owner_allowed = False
    if story and identity:
        creator = await db.get(CreatorProfile, story.creator_id)
        owner_allowed = bool(
            creator
            and creator.user_id == identity[0].id
            and story.status not in {StoryStatus.deleted, StoryStatus.removed}
        )
    if not owner_allowed:
        story = await service.public_story(db, story_id, identity[0] if identity else None)
    if not story:
        raise HTTPException(status_code=404, detail="Story not found")
    asset = await db.get(MediaAsset, story.media_asset_id)
    if (
        not asset
        or asset.owner_creator_id != story.creator_id
        or asset.status is not MediaStatus.ready
        or asset.deleted_at is not None
        or asset.moderation_status in service.UNSAFE_MODERATION_STATUSES
    ):
        raise HTTPException(status_code=404, detail="Story media not found")
    derivative = await service.delivery_derivative(db, asset)
    if not derivative:
        raise HTTPException(status_code=404, detail="Story media not found")
    configured_ttl = get_settings().media_url_ttl_seconds
    try:
        ttl = (
            configured_ttl if owner_allowed else service.public_delivery_ttl(story, configured_ttl)
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Story not found") from exc
    return RedirectResponse(storage_provider().create_download_url(derivative.storage_key, ttl))


@router.get("/{story_id}", response_model=StoryResponse)
async def public_detail(story_id: UUID, identity: OptionalIdentity, db: Db) -> StoryResponse:
    story = await service.public_story(db, story_id, identity[0] if identity else None)
    if not story:
        raise HTTPException(status_code=404, detail="Story not found")
    return await story_response(db, story)


@router.delete("/{story_id}", response_model=StoryResponse)
async def delete_story(
    story_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> StoryResponse:
    try:
        await enforce_social_rate_limit(request, str(identity[0].id), "story_delete")
        story = await service.delete_story(db, identity[0], story_id)
        await db.commit()
        return await story_response(db, story)
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
