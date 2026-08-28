from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db, OptionalIdentity
from app.compliance.http import resolve_request_compliance_decision
from app.compliance.types import ComplianceDecision
from app.content import service
from app.content.access import (
    can_access_asset,
    can_access_content,
    can_access_preview,
    content_requires_adult_access,
    public_content_surface_eligible,
)
from app.media.service import approved_creator
from app.models.compliance import ComplianceFeature
from app.models.content import (
    ContentItem,
    ContentStatus,
    DerivativeType,
    Gallery,
    GalleryItem,
    MediaAsset,
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


async def require_content_authoring(db: Db, request: Request, user) -> None:
    for feature, restricted in (
        (ComplianceFeature.platform_access, False),
        (ComplianceFeature.adult_media, True),
    ):
        decision = await resolve_request_compliance_decision(
            db,
            request,
            user=user,
            feature=feature,
            adult_restricted=restricted,
        )
        if not decision.allowed:
            raise HTTPException(
                403,
                {
                    "message": decision.reason,
                    "code": decision.code,
                    "action": decision.action,
                    "reason": decision.reason,
                },
            )


def response(
    item: ContentItem,
    has_access: bool = True,
    *,
    preview_duration_seconds: int | None = None,
) -> ContentResponse:
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
        requires_verified_consent=item.requires_verified_consent,
        published_at=item.published_at,
        preview_duration_seconds=preview_duration_seconds,
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
    content_ids = [item.id for item in items]
    preview_rows = (
        (
            await db.execute(
                select(VideoContent.content_id, VideoContent.preview_duration_seconds).where(
                    VideoContent.content_id.in_(content_ids)
                )
            )
        ).all()
        if content_ids
        else []
    )
    preview_by_content = dict(preview_rows)
    return [
        response(item, preview_duration_seconds=preview_by_content.get(item.id)) for item in items
    ]


def media_response(
    derivative: MediaDerivative,
    *,
    kind: str,
    position: int,
    preview: bool,
) -> ContentPreview:
    segment = "previews" if preview else "derivatives"
    return ContentPreview(
        derivative_id=derivative.id,
        media_type="video" if derivative.mime_type.startswith("video/") else "image",
        delivery_path=f"/media/{segment}/{derivative.id}",
        kind=kind,
        position=position,
        width=derivative.width,
        height=derivative.height,
        duration_seconds=derivative.duration_seconds,
    )


async def public_response(
    db: Db,
    item: ContentItem,
    has_access: bool,
    user=None,
    adult_decision: ComplianceDecision | None = None,
) -> ContentResponse:
    result = response(item, has_access)
    result.adult_access_required = await content_requires_adult_access(db, item)
    result.adult_access_granted = not result.adult_access_required or bool(
        adult_decision and adult_decision.age_access_allowed
    )
    result.compliance_allowed = bool(adult_decision and adult_decision.allowed)
    if adult_decision:
        result.compliance_code = adult_decision.code
        result.compliance_action = adult_decision.action if not adult_decision.allowed else None
        result.compliance_reason = adult_decision.reason
        if not adult_decision.allowed:
            result.title = (
                "Age-restricted content" if result.adult_access_required else "Content unavailable"
            )
            result.description = None
    creator = await db.get(CreatorProfile, item.owner_creator_id)
    if creator:
        result.creator_id = creator.id
        result.creator_username = creator.username
        result.creator_display_name = creator.display_name or creator.username
    gallery = await db.scalar(select(Gallery).where(Gallery.content_id == item.id))
    if gallery:
        gallery_items = (
            await db.scalars(
                select(GalleryItem)
                .where(GalleryItem.gallery_id == gallery.id)
                .order_by(GalleryItem.position)
            )
        ).all()
        result.media_count = len(gallery_items)
        preview_items = [
            gallery_item
            for gallery_item in gallery_items
            if gallery_item.is_preview or gallery_item.position < gallery.preview_count
        ]
        preview_items.sort(
            key=lambda gallery_item: (
                gallery_item.media_asset_id != gallery.cover_media_asset_id,
                gallery_item.position,
            )
        )
        preview_asset_ids = [gallery_item.media_asset_id for gallery_item in preview_items]
        preview_derivative_type = (
            DerivativeType.display
            if item.access_policy.value == "free"
            else DerivativeType.blurred_preview
        )
        derivatives = (
            await db.scalars(
                select(MediaDerivative)
                .where(
                    MediaDerivative.media_asset_id.in_(preview_asset_ids),
                    MediaDerivative.derivative_type == preview_derivative_type,
                    MediaDerivative.status == MediaStatus.ready,
                )
                .order_by(MediaDerivative.created_at)
            )
        ).all()
        derivatives_by_asset = {derivative.media_asset_id: derivative for derivative in derivatives}
        for gallery_item in preview_items:
            derivative = derivatives_by_asset.get(gallery_item.media_asset_id)
            if derivative and await can_access_preview(db, derivative, user, adult_decision):
                result.previews.append(
                    media_response(
                        derivative,
                        kind=("image" if item.access_policy.value == "free" else "teaser"),
                        position=gallery_item.position,
                        preview=True,
                    )
                )
        if has_access and gallery_items:
            full_derivatives = (
                await db.scalars(
                    select(MediaDerivative)
                    .join(MediaAsset, MediaAsset.id == MediaDerivative.media_asset_id)
                    .where(
                        MediaDerivative.media_asset_id.in_(
                            [gallery_item.media_asset_id for gallery_item in gallery_items]
                        ),
                        MediaAsset.owner_creator_id == item.owner_creator_id,
                        MediaDerivative.derivative_type == DerivativeType.display,
                        MediaDerivative.status == MediaStatus.ready,
                    )
                )
            ).all()
            full_by_asset = {
                derivative.media_asset_id: derivative for derivative in full_derivatives
            }
            for gallery_item in gallery_items:
                derivative = full_by_asset.get(gallery_item.media_asset_id)
                if derivative and await can_access_asset(
                    db, gallery_item.media_asset_id, user, adult_decision
                ):
                    result.media.append(
                        media_response(
                            derivative,
                            kind="image",
                            position=gallery_item.position,
                            preview=False,
                        )
                    )
        return result
    video = await db.scalar(select(VideoContent).where(VideoContent.content_id == item.id))
    if not video:
        return result
    result.preview_duration_seconds = video.preview_duration_seconds
    asset = await db.get(MediaAsset, video.source_media_asset_id)
    if not asset or asset.owner_creator_id != item.owner_creator_id:
        result.media_count = 0
        return result
    result.media_count = 1
    result.duration_seconds = asset.duration_seconds
    derivatives = (
        await db.scalars(
            select(MediaDerivative).where(
                MediaDerivative.media_asset_id == video.source_media_asset_id,
                MediaDerivative.derivative_type.in_(
                    [DerivativeType.poster, DerivativeType.preview_clip]
                ),
                MediaDerivative.status == MediaStatus.ready,
            )
        )
    ).all()
    preview_order = {DerivativeType.poster: 0, DerivativeType.preview_clip: 1}
    for derivative in sorted(derivatives, key=lambda value: preview_order[value.derivative_type]):
        if await can_access_preview(db, derivative, user, adult_decision):
            result.previews.append(
                media_response(
                    derivative,
                    kind=(
                        "poster"
                        if derivative.derivative_type is DerivativeType.poster
                        else "trailer"
                    ),
                    position=preview_order[derivative.derivative_type],
                    preview=True,
                )
            )
    if has_access:
        playback = await db.scalar(
            select(MediaDerivative).where(
                MediaDerivative.media_asset_id == video.source_media_asset_id,
                MediaDerivative.derivative_type == DerivativeType.playback,
                MediaDerivative.status == MediaStatus.ready,
            )
        )
        if playback and await can_access_asset(
            db, video.source_media_asset_id, user, adult_decision
        ):
            result.media = [media_response(playback, kind="playback", position=0, preview=False)]
    return result


@router.post("/galleries", response_model=ContentResponse)
async def create_gallery(
    payload: GalleryCreate, request: Request, identity: CurrentIdentity, db: Db
) -> ContentResponse:
    try:
        await require_content_authoring(db, request, identity[0])
        item = await service.create_gallery(
            db,
            identity[0],
            payload.title,
            payload.description,
            payload.access_policy,
            payload.price_amount_minor,
            payload.price_currency,
            payload.requires_verified_consent,
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
    content_id: UUID,
    payload: GalleryItemCreate,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
) -> ContentResponse:
    try:
        await require_content_authoring(db, request, identity[0])
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
async def create_video(
    payload: VideoCreate, request: Request, identity: CurrentIdentity, db: Db
) -> ContentResponse:
    try:
        await require_content_authoring(db, request, identity[0])
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
            payload.requires_verified_consent,
        )
        if not item.video:
            raise ValueError("Video content is missing")
        preview_job = (
            str(item.id),
            item.video.preview_start_seconds,
            item.video.preview_duration_seconds,
        )
        await db.commit()
        render_video_preview.delay(*preview_job)
        return response(item)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)
        ) from exc


@router.patch("/galleries/{content_id}/preview", response_model=ContentResponse)
async def configure_preview(
    content_id: UUID,
    payload: GalleryPreviewUpdate,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
) -> ContentResponse:
    try:
        await require_content_authoring(db, request, identity[0])
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
    content_id: UUID,
    payload: GalleryCoverUpdate,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
) -> ContentResponse:
    try:
        await require_content_authoring(db, request, identity[0])
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
    content_id: UUID,
    payload: GalleryOrderUpdate,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
) -> ContentResponse:
    try:
        await require_content_authoring(db, request, identity[0])
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
    content_id: UUID,
    payload: VideoPreviewUpdate,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
) -> ContentResponse:
    try:
        await require_content_authoring(db, request, identity[0])
        item = await service.configure_video_preview(
            db,
            identity[0],
            content_id,
            payload.preview_start_seconds,
            payload.preview_duration_seconds,
        )
        if not item.video:
            raise ValueError("Video content is missing")
        preview_job = (
            str(item.id),
            item.video.preview_start_seconds,
            item.video.preview_duration_seconds,
        )
        await db.commit()
        render_video_preview.delay(*preview_job)
        return response(item)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)
        ) from exc


@router.post("/{content_id}/publish", response_model=ContentResponse, deprecated=True)
@router.post("/{content_id}/submit", response_model=ContentResponse)
async def submit_for_review(
    content_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> ContentResponse:
    try:
        await require_content_authoring(db, request, identity[0])
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
    content_id: UUID,
    payload: ContentUpdate,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
) -> ContentResponse:
    try:
        await require_content_authoring(db, request, identity[0])
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
async def public_content(
    content_id: UUID, request: Request, identity: OptionalIdentity, db: Db
) -> ContentResponse:
    item = await db.scalar(select(ContentItem).where(ContentItem.id == content_id))
    user = identity[0] if identity else None
    if not item or not await public_content_surface_eligible(db, item, user):
        raise HTTPException(status_code=404, detail="Content not found")
    requires_adult = await content_requires_adult_access(db, item)
    decision = await resolve_request_compliance_decision(
        db,
        request,
        user=user,
        feature=(
            ComplianceFeature.adult_media if requires_adult else ComplianceFeature.platform_access
        ),
        adult_restricted=requires_adult,
    )
    has_access = await can_access_content(db, item, user) and decision.allowed
    return await public_response(db, item, has_access, user, decision)


@router.get("/public/by-creator/{username}", response_model=list[ContentResponse])
async def public_creator_content(
    username: str, request: Request, identity: OptionalIdentity, db: Db
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
    result = []
    user = identity[0] if identity else None
    platform_decision = await resolve_request_compliance_decision(
        db,
        request,
        user=user,
        feature=ComplianceFeature.platform_access,
        adult_restricted=False,
    )
    adult_decision = await resolve_request_compliance_decision(
        db,
        request,
        user=user,
        feature=ComplianceFeature.adult_media,
        adult_restricted=True,
    )
    for item in items:
        if not await public_content_surface_eligible(db, item, user):
            continue
        requires_adult = await content_requires_adult_access(db, item)
        decision = adult_decision if requires_adult else platform_decision
        has_access = await can_access_content(db, item, user) and decision.allowed
        result.append(await public_response(db, item, has_access, user, decision))
    return result
