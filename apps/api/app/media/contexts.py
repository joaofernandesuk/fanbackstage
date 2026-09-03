"""Authoritative media-context isolation for protected derivative delivery."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import Gallery, GalleryItem, MediaAsset, VideoContent
from app.models.creator import CreatorProfileMedia
from app.models.marketplace import MarketplaceListingMedia
from app.models.messaging import MessageAttachment
from app.models.social import FeedPostMedia
from app.models.story import Story

MediaContext = tuple[str, UUID]


class MediaContextConflict(ValueError):
    pass


async def media_asset_contexts(
    db: AsyncSession, asset_id: UUID, *, lock_asset: bool = False
) -> set[MediaContext]:
    """Return every persisted surface context that references one media asset."""

    asset_query = select(MediaAsset.id).where(MediaAsset.id == asset_id)
    if lock_asset:
        asset_query = asset_query.with_for_update()
    if await db.scalar(asset_query) is None:
        return set()

    contexts: set[MediaContext] = set()
    content_ids = set(
        await db.scalars(
            select(VideoContent.content_id).where(VideoContent.source_media_asset_id == asset_id)
        )
    )
    content_ids.update(
        await db.scalars(
            select(Gallery.content_id)
            .join(GalleryItem, GalleryItem.gallery_id == Gallery.id)
            .where(GalleryItem.media_asset_id == asset_id)
        )
    )
    content_ids.update(
        await db.scalars(select(Gallery.content_id).where(Gallery.cover_media_asset_id == asset_id))
    )
    contexts.update(("content", content_id) for content_id in content_ids)
    contexts.update(
        ("feed", post_id)
        for post_id in await db.scalars(
            select(FeedPostMedia.post_id).where(FeedPostMedia.media_asset_id == asset_id)
        )
    )
    contexts.update(
        ("message", message_id)
        for message_id in await db.scalars(
            select(MessageAttachment.message_id).where(MessageAttachment.media_asset_id == asset_id)
        )
    )
    contexts.update(
        ("story", story_id)
        for story_id in await db.scalars(select(Story.id).where(Story.media_asset_id == asset_id))
    )
    contexts.update(
        ("profile", profile_media_id)
        for profile_media_id in await db.scalars(
            select(CreatorProfileMedia.id).where(CreatorProfileMedia.media_asset_id == asset_id)
        )
    )
    contexts.update(
        ("marketplace", listing_id)
        for listing_id in await db.scalars(
            select(MarketplaceListingMedia.listing_id).where(
                MarketplaceListingMedia.media_asset_id == asset_id
            )
        )
    )
    return contexts


async def require_media_context_available(
    db: AsyncSession,
    asset_id: UUID,
    *,
    context_type: str,
    context_id: UUID | None = None,
) -> None:
    """Serialize attachment and reject reuse outside the one intended context."""

    contexts = await media_asset_contexts(db, asset_id, lock_asset=True)
    allowed = {(context_type, context_id)} if context_id is not None else set()
    if contexts - allowed:
        raise MediaContextConflict(
            "Media assets must be dedicated to one content or communication context"
        )


async def has_single_media_context(db: AsyncSession, asset_id: UUID) -> bool:
    """Final delivery is unavailable for unowned or ambiguous media references."""

    return len(await media_asset_contexts(db, asset_id)) == 1
