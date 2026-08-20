from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db, OptionalIdentity
from app.content import service
from app.content.access import can_access_content
from app.models.content import ContentItem
from app.schemas.content import (
    ContentResponse,
    ContentUpdate,
    GalleryCreate,
    GalleryItemCreate,
    GalleryOrderUpdate,
    GalleryPreviewUpdate,
    VideoCreate,
)

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


@router.patch("/galleries/{content_id}/preview", response_model=ContentResponse)
async def configure_preview(
    content_id: UUID, payload: GalleryPreviewUpdate, identity: CurrentIdentity, db: Db
) -> ContentResponse:
    try:
        item = await service.configure_gallery_preview(
            db, identity[0], content_id, payload.preview_count, set(payload.preview_asset_ids)
        )
        await db.commit()
        return response(item)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)
        ) from exc


@router.patch("/galleries/{content_id}/order", response_model=ContentResponse)
async def reorder(
    content_id: UUID, payload: GalleryOrderUpdate, identity: CurrentIdentity, db: Db
) -> ContentResponse:
    try:
        item = await service.reorder_gallery(db, identity[0], content_id, payload.media_asset_ids)
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


@router.patch("/{content_id}", response_model=ContentResponse)
async def update_content(
    content_id: UUID, payload: ContentUpdate, identity: CurrentIdentity, db: Db
) -> ContentResponse:
    try:
        item = await service.update_content(
            db, identity[0], content_id, payload.model_dump(exclude_unset=True)
        )
        await db.commit()
        return response(item)
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/public/{content_id}", response_model=ContentResponse)
async def public_content(content_id: UUID, identity: OptionalIdentity, db: Db) -> ContentResponse:
    item = await db.scalar(select(ContentItem).where(ContentItem.id == content_id))
    if not item or item.status.value != "published":
        raise HTTPException(status_code=404, detail="Content not found")
    has_access = await can_access_content(db, item, identity[0] if identity else None)
    return response(item, has_access)
