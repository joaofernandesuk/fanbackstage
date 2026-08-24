from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.audit.service import record_event
from app.media.service import approved_creator, asset_for_owner
from app.models.content import (
    AccessPolicy,
    ContentItem,
    ContentStatus,
    ContentType,
    DerivativeType,
    Gallery,
    GalleryItem,
    MediaAsset,
    MediaDerivative,
    MediaStatus,
    MediaType,
    ModerationStatus,
    VideoContent,
)
from app.models.identity import User


def validate_ppv_price(
    policy: AccessPolicy, price_amount_minor: int | None, price_currency: str | None
) -> None:
    if policy is AccessPolicy.ppv and (not price_amount_minor or not price_currency):
        raise ValueError("PPV content requires a price and currency")
    if policy is not AccessPolicy.ppv and (
        price_amount_minor is not None or price_currency is not None
    ):
        raise ValueError("Prices are only valid for PPV content")


async def create_gallery(
    db: AsyncSession,
    user: User,
    title: str,
    description: str | None,
    policy: AccessPolicy,
    price_amount_minor: int | None = None,
    price_currency: str | None = None,
) -> ContentItem:
    creator = await approved_creator(db, user)
    validate_ppv_price(policy, price_amount_minor, price_currency)
    content = ContentItem(
        owner_creator_id=creator.id,
        created_by_user_id=user.id,
        content_type=ContentType.gallery,
        title=title,
        description=description,
        access_policy=policy,
        price_amount_minor=price_amount_minor,
        price_currency=price_currency.upper() if price_currency else None,
    )
    content.gallery = Gallery()
    db.add(content)
    await db.flush()
    return content


async def add_gallery_item(
    db: AsyncSession, user: User, content_id: UUID, asset_id: UUID, preview: bool = False
) -> ContentItem:
    content = await owned_content(db, user, content_id, ContentType.gallery)
    asset = await asset_for_owner(db, user, asset_id)
    if asset.media_type is not MediaType.image:
        raise ValueError("Galleries require image assets")
    assert content.gallery
    next_position = len(content.gallery.items)
    content.gallery.items.append(
        GalleryItem(media_asset_id=asset.id, position=next_position, is_preview=preview)
    )
    if not content.gallery.cover_media_asset_id:
        content.gallery.cover_media_asset_id = asset.id
    if content.status is ContentStatus.draft:
        content.status = ContentStatus.processing
    return content


async def create_video(
    db: AsyncSession,
    user: User,
    title: str,
    description: str | None,
    asset_id: UUID,
    policy: AccessPolicy,
    preview_start_seconds: int = 0,
    preview_duration_seconds: int = 20,
    price_amount_minor: int | None = None,
    price_currency: str | None = None,
) -> ContentItem:
    creator = await approved_creator(db, user)
    validate_ppv_price(policy, price_amount_minor, price_currency)
    asset = await asset_for_owner(db, user, asset_id)
    if asset.media_type is not MediaType.video:
        raise ValueError("Videos require video assets")
    validate_video_preview(asset, preview_start_seconds, preview_duration_seconds)
    content = ContentItem(
        owner_creator_id=creator.id,
        created_by_user_id=user.id,
        content_type=ContentType.video,
        title=title,
        description=description,
        access_policy=policy,
        price_amount_minor=price_amount_minor,
        price_currency=price_currency.upper() if price_currency else None,
        status=ContentStatus.processing,
    )
    content.video = VideoContent(
        source_media_asset_id=asset.id,
        preview_start_seconds=preview_start_seconds,
        preview_duration_seconds=preview_duration_seconds,
    )
    db.add(content)
    await db.flush()
    await mark_video_preview_queued(db, asset.id)
    return content


async def owned_content(
    db: AsyncSession, user: User, content_id: UUID, expected: ContentType | None = None
) -> ContentItem:
    creator = await approved_creator(db, user)
    content = await db.scalar(
        select(ContentItem)
        .options(
            selectinload(ContentItem.gallery).selectinload(Gallery.items),
            selectinload(ContentItem.video),
        )
        .where(ContentItem.id == content_id)
    )
    if (
        not content
        or content.owner_creator_id != creator.id
        or (expected and content.content_type is not expected)
    ):
        raise PermissionError("Content not found")
    return content


async def submit_for_review(db: AsyncSession, user: User, content_id: UUID) -> ContentItem:
    content = await owned_content(db, user, content_id)
    if content.status is not ContentStatus.processing:
        raise ValueError("Only processing content can be submitted for review")
    if content.content_type is ContentType.gallery:
        assert content.gallery
        assets = [await db.get(MediaAsset, item.media_asset_id) for item in content.gallery.items]
        if not assets or any(
            not asset or asset.status is not MediaStatus.ready for asset in assets
        ):
            raise ValueError("All gallery media must be ready before publishing")
    else:
        assert content.video
        asset = await asset_for_owner(db, user, content.video.source_media_asset_id)
        if asset.status is not MediaStatus.ready:
            raise ValueError("Video media must be ready before publishing")
        preview = await db.scalar(
            select(MediaDerivative).where(
                MediaDerivative.media_asset_id == asset.id,
                MediaDerivative.derivative_type == DerivativeType.preview_clip,
            )
        )
        if not preview or preview.status is not MediaStatus.ready:
            raise ValueError("Video preview is still processing")
    content.status = ContentStatus.pending_review
    content.moderation_status = ModerationStatus.queued
    await record_event(
        db,
        "content.submitted_for_review",
        actor_user_id=user.id,
        target_type="content_item",
        target_id=str(content.id),
    )
    return content


async def apply_moderation_containment(
    db: AsyncSession, actor: User, content_id: UUID, reason: str
) -> ContentItem:
    """Authoritative reversible content containment for Trust & Safety."""
    content = await db.get(ContentItem, content_id)
    if not content:
        raise ValueError("Content not found")
    content.status = ContentStatus.removed
    content.moderation_status = ModerationStatus.removed
    await record_event(
        db,
        "content.moderation_contained",
        actor_user_id=actor.id,
        target_type="content_item",
        target_id=str(content.id),
        metadata={"reason": reason},
    )
    return content


async def restore_from_moderation(
    db: AsyncSession, actor: User, content_id: UUID, reason: str
) -> ContentItem:
    content = await db.get(ContentItem, content_id)
    if not content:
        raise ValueError("Content not found")
    content.status = ContentStatus.pending_review
    content.moderation_status = ModerationStatus.queued
    await record_event(
        db,
        "content.moderation_restored",
        actor_user_id=actor.id,
        target_type="content_item",
        target_id=str(content.id),
        metadata={"reason": reason},
    )
    return content


async def approve(db: AsyncSession, content: ContentItem, actor: User) -> ContentItem:
    if content.status is not ContentStatus.pending_review:
        raise ValueError("Only content pending review can be approved")
    content.status, content.published_at = ContentStatus.published, datetime.now(UTC)
    content.moderation_status = ModerationStatus.approved
    await record_event(
        db,
        "content.approved",
        actor_user_id=actor.id,
        target_type="content_item",
        target_id=str(content.id),
    )
    from app.social.service import auto_post_content

    await auto_post_content(db, content)
    return content


async def reject(db: AsyncSession, content: ContentItem, actor: User) -> ContentItem:
    if content.status is not ContentStatus.pending_review:
        raise ValueError("Only content pending review can be rejected")
    content.status = ContentStatus.rejected
    content.moderation_status = ModerationStatus.rejected
    await record_event(
        db,
        "content.rejected",
        actor_user_id=actor.id,
        target_type="content_item",
        target_id=str(content.id),
    )
    return content


async def archive(db: AsyncSession, user: User, content_id: UUID) -> ContentItem:
    content = await owned_content(db, user, content_id)
    content.status = ContentStatus.archived
    return content


async def update_content(
    db: AsyncSession, user: User, content_id: UUID, values: dict[str, object]
) -> ContentItem:
    content = await owned_content(db, user, content_id)
    target_policy = values.get("access_policy", content.access_policy)
    target_amount = values.get("price_amount_minor", content.price_amount_minor)
    target_currency = values.get("price_currency", content.price_currency)
    validate_ppv_price(target_policy, target_amount, target_currency)
    for field in (
        "title",
        "description",
        "access_policy",
        "price_amount_minor",
        "price_currency",
        "feed_announcement_override",
    ):
        if field in values:
            value = values[field]
            setattr(content, field, value.upper() if field == "price_currency" and value else value)
    return content


async def update_content_as_group_manager(
    db: AsyncSession, user: User, content_id: UUID, values: dict[str, object]
) -> ContentItem:
    """Apply an explicitly delegated content-management action without transferring ownership."""
    content = await db.get(ContentItem, content_id)
    if not content:
        raise PermissionError("Content not found")
    from app.groups.service import has_delegated_permission
    from app.models.groups import GroupPermission

    if not await has_delegated_permission(
        db, user.id, content.owner_creator_id, GroupPermission.manage_content
    ):
        raise PermissionError("Delegated content management permission denied")
    target_policy = values.get("access_policy", content.access_policy)
    target_amount = values.get("price_amount_minor", content.price_amount_minor)
    target_currency = values.get("price_currency", content.price_currency)
    validate_ppv_price(target_policy, target_amount, target_currency)
    for field in ("title", "description", "access_policy", "price_amount_minor", "price_currency"):
        if field in values:
            value = values[field]
            setattr(content, field, value.upper() if field == "price_currency" and value else value)
    await record_event(
        db,
        "group_manager.content_updated",
        actor_user_id=user.id,
        target_type="content_item",
        target_id=str(content.id),
    )
    return content


async def configure_gallery_preview(
    db: AsyncSession, user: User, content_id: UUID, preview_count: int, preview_asset_ids: set[UUID]
) -> ContentItem:
    content = await owned_content(db, user, content_id, ContentType.gallery)
    assert content.gallery
    items = (
        await db.scalars(select(GalleryItem).where(GalleryItem.gallery_id == content.gallery.id))
    ).all()
    if preview_count < 0 or preview_count > len(items):
        raise ValueError("Preview count is invalid")
    item_asset_ids = {item.media_asset_id for item in items}
    if not preview_asset_ids <= item_asset_ids:
        raise ValueError("Preview media must belong to the gallery")
    content.gallery.preview_count = preview_count
    for item in items:
        item.is_preview = item.media_asset_id in preview_asset_ids
    return content


async def configure_gallery_cover(
    db: AsyncSession, user: User, content_id: UUID, asset_id: UUID
) -> ContentItem:
    content = await owned_content(db, user, content_id, ContentType.gallery)
    assert content.gallery
    item = await db.scalar(
        select(GalleryItem).where(
            GalleryItem.gallery_id == content.gallery.id,
            GalleryItem.media_asset_id == asset_id,
        )
    )
    if not item:
        raise ValueError("Cover media must belong to the gallery")
    content.gallery.cover_media_asset_id = item.media_asset_id
    return content


def validate_video_preview(asset: MediaAsset, start: int, duration: int) -> None:
    if asset.duration_seconds is not None and start + duration > asset.duration_seconds:
        raise ValueError("Video preview must fit within the video duration")


async def mark_video_preview_queued(db: AsyncSession, asset_id: UUID) -> None:
    preview = await db.scalar(
        select(MediaDerivative).where(
            MediaDerivative.media_asset_id == asset_id,
            MediaDerivative.derivative_type == DerivativeType.preview_clip,
        )
    )
    if preview:
        preview.status = MediaStatus.queued


async def configure_video_preview(
    db: AsyncSession, user: User, content_id: UUID, start: int, duration: int
) -> ContentItem:
    content = await owned_content(db, user, content_id, ContentType.video)
    if content.status not in {ContentStatus.draft, ContentStatus.processing}:
        raise ValueError("Video preview can only be changed before review")
    assert content.video
    asset = await asset_for_owner(db, user, content.video.source_media_asset_id)
    validate_video_preview(asset, start, duration)
    content.video.preview_start_seconds = start
    content.video.preview_duration_seconds = duration
    await mark_video_preview_queued(db, asset.id)
    return content


async def reorder_gallery(
    db: AsyncSession, user: User, content_id: UUID, asset_ids: list[UUID]
) -> ContentItem:
    content = await owned_content(db, user, content_id, ContentType.gallery)
    assert content.gallery
    items = (
        await db.scalars(select(GalleryItem).where(GalleryItem.gallery_id == content.gallery.id))
    ).all()
    by_asset = {item.media_asset_id: item for item in items}
    if len(asset_ids) != len(items) or set(asset_ids) != set(by_asset):
        raise ValueError("Gallery order must include every gallery item exactly once")
    for position, asset_id in enumerate(asset_ids):
        by_asset[asset_id].position = position + len(items)
    await db.flush()
    for position, asset_id in enumerate(asset_ids):
        by_asset[asset_id].position = position
    return content
