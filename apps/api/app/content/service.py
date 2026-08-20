from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.media.service import approved_creator, asset_for_owner
from app.models.content import (
    AccessPolicy,
    ContentItem,
    ContentStatus,
    ContentType,
    Gallery,
    GalleryItem,
    MediaAsset,
    MediaStatus,
    MediaType,
    VideoContent,
)
from app.models.identity import User


async def create_gallery(
    db: AsyncSession, user: User, title: str, description: str | None, policy: AccessPolicy
) -> ContentItem:
    creator = await approved_creator(db, user)
    content = ContentItem(
        owner_creator_id=creator.id,
        created_by_user_id=user.id,
        content_type=ContentType.gallery,
        title=title,
        description=description,
        access_policy=policy,
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
    return content


async def create_video(
    db: AsyncSession,
    user: User,
    title: str,
    description: str | None,
    asset_id: UUID,
    policy: AccessPolicy,
) -> ContentItem:
    creator = await approved_creator(db, user)
    asset = await asset_for_owner(db, user, asset_id)
    if asset.media_type is not MediaType.video:
        raise ValueError("Videos require video assets")
    content = ContentItem(
        owner_creator_id=creator.id,
        created_by_user_id=user.id,
        content_type=ContentType.video,
        title=title,
        description=description,
        access_policy=policy,
    )
    content.video = VideoContent(source_media_asset_id=asset.id)
    db.add(content)
    await db.flush()
    return content


async def owned_content(
    db: AsyncSession, user: User, content_id: UUID, expected: ContentType | None = None
) -> ContentItem:
    creator = await approved_creator(db, user)
    content = await db.get(ContentItem, content_id)
    if (
        not content
        or content.owner_creator_id != creator.id
        or (expected and content.content_type is not expected)
    ):
        raise PermissionError("Content not found")
    return content


async def publish(db: AsyncSession, user: User, content_id: UUID) -> ContentItem:
    content = await owned_content(db, user, content_id)
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
    content.status, content.published_at = ContentStatus.published, datetime.now(UTC)
    return content


async def archive(db: AsyncSession, user: User, content_id: UUID) -> ContentItem:
    content = await owned_content(db, user, content_id)
    content.status = ContentStatus.archived
    return content


async def update_content(
    db: AsyncSession, user: User, content_id: UUID, values: dict[str, object]
) -> ContentItem:
    content = await owned_content(db, user, content_id)
    for field in ("title", "description", "access_policy"):
        if field in values:
            setattr(content, field, values[field])
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
