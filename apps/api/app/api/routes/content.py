from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db, OptionalIdentity
from app.content import service
from app.content.access import can_access_content
from app.media.service import approved_creator
from app.models.content import (
    ContentItem,
    ContentStatus,
    DerivativeType,
    Gallery,
    GalleryItem,
    MediaDerivative,
    MediaStatus,
    VideoContent,
)
from app.models.creator import CreatorProfile, CreatorStatus
from app.schemas.content import (
    ContentPreview,
    ContentResponse,
    ContentUpdate,
    GalleryCoverUpdate,
    GalleryCreate,
    GalleryItemCreate,
    GalleryOrderUpdate,
    GalleryPreviewUpdate,
    VideoCreate,
    VideoPreviewUpdate,
)
from app.worker.tasks import render_video_preview

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
        price_amount_minor=item.price_amount_minor if item.access_policy.value == "ppv" else None,
        price_currency=item.price_currency if item.access_policy.value == "ppv" else None,
    )


@router.get("/mine", response_model=list[ContentResponse])
async def my_content(identity: CurrentIdentity, db: Db) -> list[ContentResponse]:
    creator = await approved_creator(db, identity[0])
    items = (
        await db.scalars(
            select(ContentItem)
            .where(ContentItem.owner_creator_id == creator.id)
            .order_by(ContentItem.created_at.desc())
        )
    ).all()
    return [response(item) for item in items]


async def public_response(db: Db, item: ContentItem, has_access: bool) -> ContentResponse:
    result = response(item, has_access)
    gallery = await db.scalar(select(Gallery).where(Gallery.content_id == item.id))
    if gallery:
        gallery_items = (
            await db.scalars(
                select(GalleryItem)
                .where(GalleryItem.gallery_id == gallery.id)
                .order_by(GalleryItem.position)
            )
        ).all()
        preview_asset_ids = [
            gallery_item.media_asset_id
            for gallery_item in gallery_items
            if gallery_item.is_preview or gallery_item.position < gallery.preview_count
        ]
        if not preview_asset_ids:
            return result
        derivatives = (
            await db.scalars(
                select(MediaDerivative)
                .where(
                    MediaDerivative.media_asset_id.in_(preview_asset_ids),
                    MediaDerivative.derivative_type == DerivativeType.blurred_preview,
                    MediaDerivative.status == MediaStatus.ready,
                )
                .order_by(MediaDerivative.created_at)
            )
        ).all()
        derivatives_by_asset = {derivative.media_asset_id: derivative for derivative in derivatives}
        result.previews = [
            ContentPreview(
                derivative_id=derivative.id,
                media_type="image",
                delivery_path=f"/media/previews/{derivative.id}",
            )
            for asset_id in preview_asset_ids
            if (derivative := derivatives_by_asset.get(asset_id))
        ]
        return result
    video = await db.scalar(select(VideoContent).where(VideoContent.content_id == item.id))
    if not video:
        return result
    derivative = await db.scalar(
        select(MediaDerivative).where(
            MediaDerivative.media_asset_id == video.source_media_asset_id,
            MediaDerivative.derivative_type == DerivativeType.poster,
            MediaDerivative.status == MediaStatus.ready,
        )
    )
    if derivative:
        result.previews = [
            ContentPreview(
                derivative_id=derivative.id,
                media_type="image",
                delivery_path=f"/media/previews/{derivative.id}",
            )
        ]
    return result


@router.post("/galleries", response_model=ContentResponse)
async def create_gallery(
    payload: GalleryCreate, identity: CurrentIdentity, db: Db
) -> ContentResponse:
    try:
        item = await service.create_gallery(
            db,
            identity[0],
            payload.title,
            payload.description,
            payload.access_policy,
            payload.price_amount_minor,
            payload.price_currency,
        )
        await db.commit()
        return response(item)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)
        ) from exc


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
            payload.preview_start_seconds,
            payload.preview_duration_seconds,
            payload.price_amount_minor,
            payload.price_currency,
        )
        await db.commit()
        render_video_preview.delay(str(item.id))
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


@router.patch("/galleries/{content_id}/cover", response_model=ContentResponse)
async def configure_cover(
    content_id: UUID, payload: GalleryCoverUpdate, identity: CurrentIdentity, db: Db
) -> ContentResponse:
    try:
        item = await service.configure_gallery_cover(
            db, identity[0], content_id, payload.media_asset_id
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


@router.patch("/videos/{content_id}/preview", response_model=ContentResponse)
async def configure_video_preview(
    content_id: UUID, payload: VideoPreviewUpdate, identity: CurrentIdentity, db: Db
) -> ContentResponse:
    try:
        item = await service.configure_video_preview(
            db,
            identity[0],
            content_id,
            payload.preview_start_seconds,
            payload.preview_duration_seconds,
        )
        await db.commit()
        render_video_preview.delay(str(item.id))
        return response(item)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)
        ) from exc


@router.post("/{content_id}/publish", response_model=ContentResponse, deprecated=True)
@router.post("/{content_id}/submit", response_model=ContentResponse)
async def submit_for_review(content_id: UUID, identity: CurrentIdentity, db: Db) -> ContentResponse:
    try:
        item = await service.submit_for_review(db, identity[0], content_id)
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
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)
        ) from exc


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
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)
        ) from exc


@router.get("/public/{content_id}", response_model=ContentResponse)
async def public_content(content_id: UUID, identity: OptionalIdentity, db: Db) -> ContentResponse:
    item = await db.scalar(select(ContentItem).where(ContentItem.id == content_id))
    if not item or item.status.value != "published":
        raise HTTPException(status_code=404, detail="Content not found")
    has_access = await can_access_content(db, item, identity[0] if identity else None)
    return await public_response(db, item, has_access)


@router.get("/public/by-creator/{username}", response_model=list[ContentResponse])
async def public_creator_content(
    username: str, identity: OptionalIdentity, db: Db
) -> list[ContentResponse]:
    items = (
        (
            await db.scalars(
                select(ContentItem)
                .join(CreatorProfile, CreatorProfile.id == ContentItem.owner_creator_id)
                .where(
                    CreatorProfile.username == username.lower(),
                    CreatorProfile.status == CreatorStatus.approved,
                    CreatorProfile.is_public.is_(True),
                    ContentItem.status == ContentStatus.published,
                )
                .order_by(ContentItem.published_at.desc(), ContentItem.created_at.desc())
            )
        )
        .unique()
        .all()
    )
    return [
        await public_response(
            db, item, await can_access_content(db, item, identity[0] if identity else None)
        )
        for item in items
    ]
