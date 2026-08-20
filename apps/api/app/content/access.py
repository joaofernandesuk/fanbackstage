from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import (
    AccessPolicy,
    ContentEntitlement,
    ContentItem,
    ContentStatus,
    EntitlementStatus,
)
from app.models.creator import CreatorProfile
from app.models.identity import User


async def can_access_content(db: AsyncSession, content: ContentItem, user: User | None) -> bool:
    """Fail closed for all non-free policies; routes must use this resolver."""
    if content.status is not ContentStatus.published:
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
    entitlement = await db.scalar(
        select(ContentEntitlement.id).where(
            ContentEntitlement.subject_user_id == user.id,
            ContentEntitlement.content_id == content.id,
            ContentEntitlement.status == EntitlementStatus.active,
            ContentEntitlement.valid_from <= now,
            or_(ContentEntitlement.valid_until.is_(None), ContentEntitlement.valid_until > now),
        )
    )
    return entitlement is not None


async def can_access_asset(db: AsyncSession, asset_id: UUID, user: User | None) -> bool:
    """Only an owning or entitled published content item can authorize full media."""
    from app.models.content import Gallery, GalleryItem, VideoContent

    content = await db.scalar(
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
    return bool(content and await can_access_content(db, content, user))
