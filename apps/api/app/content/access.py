from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import (
    AccessPolicy,
    ContentEntitlement,
    ContentItem,
    ContentStatus,
    DerivativeType,
    EntitlementStatus,
    Gallery,
    GalleryItem,
    MediaAsset,
    MediaDerivative,
    MediaStatus,
    VideoContent,
)
from app.models.creator import CreatorProfile
from app.models.identity import User


async def can_access_content(db: AsyncSession, content: ContentItem, user: User | None) -> bool:
    """Fail closed for all non-free policies; routes must use this resolver."""
    if content.status is not ContentStatus.published or content.moderation_status.name in {
        "flagged",
        "rejected",
        "removed",
    }:
        return False
    if user:
        owner = await db.scalar(select(CreatorProfile.id).where(CreatorProfile.user_id == user.id))
        if owner == content.owner_creator_id or {role.name for role in user.roles} & {
            "admin",
            "moderator",
            "super_admin",
        }:
            return True
    if content.access_policy is AccessPolicy.free:
        return True
    if not user:
        return False
    now = datetime.now(UTC)
    scope = ContentEntitlement.content_id == content.id
    if content.access_policy is AccessPolicy.subscription:
        scope = ContentEntitlement.creator_id == content.owner_creator_id
    entitlement = await db.scalar(
        select(ContentEntitlement.id).where(
            ContentEntitlement.subject_user_id == user.id,
            scope,
            ContentEntitlement.status == EntitlementStatus.active,
            ContentEntitlement.valid_from <= now,
            or_(ContentEntitlement.valid_until.is_(None), ContentEntitlement.valid_until > now),
        )
    )
    return entitlement is not None


async def can_access_asset(db: AsyncSession, asset_id: UUID, user: User | None) -> bool:
    """Only an owning or entitled published content item can authorize full media."""
    asset = await db.get(MediaAsset, asset_id)
    if (
        not asset
        or asset.status is not MediaStatus.ready
        or asset.deleted_at is not None
        or asset.moderation_status.name in {"flagged", "rejected", "removed"}
    ):
        return False
    contents = (
        (
            await db.scalars(
                select(ContentItem)
                .outerjoin(VideoContent, VideoContent.content_id == ContentItem.id)
                .outerjoin(Gallery, Gallery.content_id == ContentItem.id)
                .outerjoin(GalleryItem, GalleryItem.gallery_id == Gallery.id)
                .where(
                    or_(
                        VideoContent.source_media_asset_id == asset_id,
                        GalleryItem.media_asset_id == asset_id,
                    )
                )
            )
        )
        .unique()
        .all()
    )
    for content in contents:
        if await can_access_content(db, content, user):
            return True
    return False


async def can_access_preview(db: AsyncSession, derivative: MediaDerivative) -> bool:
    """A preview is public only when it belongs to published, ready content and is configured."""
    if derivative.status is not MediaStatus.ready:
        return False
    asset = await db.get(MediaAsset, derivative.media_asset_id)
    if (
        not asset
        or asset.status is not MediaStatus.ready
        or asset.deleted_at is not None
        or asset.moderation_status.name in {"flagged", "rejected", "removed"}
    ):
        return False
    video = await db.scalar(
        select(VideoContent).where(VideoContent.source_media_asset_id == asset.id)
    )
    if video:
        content = await db.get(ContentItem, video.content_id)
        return bool(
            content
            and content.status is ContentStatus.published
            and derivative.derivative_type in {DerivativeType.poster, DerivativeType.preview_clip}
        )
    row = await db.execute(
        select(ContentItem, Gallery, GalleryItem)
        .join(Gallery, Gallery.content_id == ContentItem.id)
        .join(GalleryItem, GalleryItem.gallery_id == Gallery.id)
        .where(GalleryItem.media_asset_id == asset.id)
    )
    for content, gallery, item in row:
        configured = item.is_preview or item.position < gallery.preview_count
        if (
            content.status is ContentStatus.published
            and configured
            and derivative.derivative_type is DerivativeType.blurred_preview
        ):
            return True
    return False
