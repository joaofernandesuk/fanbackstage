from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentIdentity, Db
from app.content import service
from app.models.content import ContentItem
from app.schemas.content import ContentResponse, GalleryCreate, GalleryItemCreate, VideoCreate

router = APIRouter(prefix="/content", tags=["content"])


def response(item: ContentItem, has_access: bool = True) -> ContentResponse:
    return ContentResponse(
        id=item.id,
        content_type=item.content_type.value,
        title=item.title,
        description=item.description,
        status=item.status.value,
        access_policy=item.access_policy.value,
        has_access=has_access,
        locked=not has_access,
    )


@router.post("/galleries", response_model=ContentResponse)
async def create_gallery(
    payload: GalleryCreate, identity: CurrentIdentity, db: Db
) -> ContentResponse:
    try:
        item = await service.create_gallery(
            db, identity[0], payload.title, payload.description, payload.access_policy
        )
        await db.commit()
        return response(item)
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/galleries/{content_id}/items", response_model=ContentResponse)
async def add_gallery_item(
    content_id: UUID, payload: GalleryItemCreate, identity: CurrentIdentity, db: Db
) -> ContentResponse:
    try:
        item = await service.add_gallery_item(
            db, identity[0], content_id, payload.media_asset_id, payload.is_preview
        )
        await db.commit()
        return response(item)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)
        ) from exc


@router.post("/videos", response_model=ContentResponse)
async def create_video(payload: VideoCreate, identity: CurrentIdentity, db: Db) -> ContentResponse:
    try:
        item = await service.create_video(
            db,
            identity[0],
            payload.title,
            payload.description,
            payload.media_asset_id,
            payload.access_policy,
        )
        await db.commit()
        return response(item)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)
        ) from exc


@router.post("/{content_id}/publish", response_model=ContentResponse)
async def publish(content_id: UUID, identity: CurrentIdentity, db: Db) -> ContentResponse:
    try:
        item = await service.publish(db, identity[0], content_id)
        await db.commit()
        return response(item)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)
        ) from exc


@router.post("/{content_id}/archive", response_model=ContentResponse)
async def archive(content_id: UUID, identity: CurrentIdentity, db: Db) -> ContentResponse:
    try:
        item = await service.archive(db, identity[0], content_id)
        await db.commit()
        return response(item)
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc
