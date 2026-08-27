from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.adult_access import AdultAccessDecision, resolve_adult_access
from app.audit.service import record_event
from app.creators.service import (
    current_adult_verification_predicate,
    require_current_adult_verification,
)
from app.media.service import approved_creator
from app.models.content import (
    AccessPolicy,
    ContentEntitlement,
    DerivativeType,
    EntitlementStatus,
    Gallery,
    GalleryItem,
    MediaAsset,
    MediaAudience,
    MediaDerivative,
    MediaStatus,
    MediaType,
    ModerationStatus,
    VideoContent,
)
from app.models.creator import CreatorProfile, CreatorStatus
from app.models.identity import User
from app.models.marketplace import MarketplaceListingMedia
from app.models.messaging import MessageAttachment, UserBlock
from app.models.social import FeedPostMedia, Follow
from app.models.story import Story, StoryStatus

STORY_LIFETIME = timedelta(hours=24)
SUPPORTED_ACCESS_POLICIES = {
    AccessPolicy.free,
    AccessPolicy.followers,
    AccessPolicy.subscription,
}
UNSAFE_MODERATION_STATUSES = {
    ModerationStatus.flagged,
    ModerationStatus.rejected,
    ModerationStatus.removed,
}


def external_asset_reference(asset_id):
    """Fail closed when another domain already owns delivery semantics for an asset."""
    return or_(
        exists(select(VideoContent.id).where(VideoContent.source_media_asset_id == asset_id)),
        exists(select(GalleryItem.id).where(GalleryItem.media_asset_id == asset_id)),
        exists(select(Gallery.id).where(Gallery.cover_media_asset_id == asset_id)),
        exists(select(FeedPostMedia.id).where(FeedPostMedia.media_asset_id == asset_id)),
        exists(
            select(MarketplaceListingMedia.id).where(
                MarketplaceListingMedia.media_asset_id == asset_id
            )
        ),
        exists(select(MessageAttachment.id).where(MessageAttachment.media_asset_id == asset_id)),
    )


async def asset_is_story_safe(db: AsyncSession, asset_id: UUID) -> bool:
    return not bool(await db.scalar(select(external_asset_reference(asset_id))))


def required_derivative_type(media_type: MediaType) -> DerivativeType:
    if media_type is MediaType.image:
        return DerivativeType.display
    if media_type is MediaType.video:
        return DerivativeType.preview_clip
    raise ValueError("Unsupported Story media type")


async def delivery_derivative(db: AsyncSession, asset: MediaAsset) -> MediaDerivative | None:
    return await db.scalar(
        select(MediaDerivative).where(
            MediaDerivative.media_asset_id == asset.id,
            MediaDerivative.derivative_type == required_derivative_type(asset.media_type),
            MediaDerivative.status == MediaStatus.ready,
        )
    )


def public_delivery_ttl(
    story: Story,
    configured_ttl_seconds: int,
    *,
    now: datetime | None = None,
) -> int:
    """Never issue a public media capability that survives the Story lifecycle."""
    remaining = int((story.expires_at - (now or datetime.now(UTC))).total_seconds())
    ttl = min(configured_ttl_seconds, remaining)
    if ttl <= 0:
        raise ValueError("Story has expired")
    return ttl


async def create_story(
    db: AsyncSession,
    user: User,
    media_asset_id: UUID,
    caption: str | None,
    alt_text: str | None,
    access_policy: AccessPolicy,
    idempotency_key: str,
    *,
    now: datetime | None = None,
) -> Story:
    """Publish one Story card at ``now``; the optional clock is for internal seed/tests only."""
    creator = await db.scalar(
        select(CreatorProfile)
        .where(
            CreatorProfile.user_id == user.id,
            CreatorProfile.status == CreatorStatus.approved,
        )
        .with_for_update()
    )
    if not creator:
        raise PermissionError("An approved creator profile is required")
    try:
        await require_current_adult_verification(db, creator.id)
    except ValueError as exc:
        raise PermissionError("A current verified adult creator profile is required") from exc
    normalized_key = idempotency_key.strip()
    if not 8 <= len(normalized_key) <= 128:
        raise ValueError("A valid Idempotency-Key is required")
    existing = await db.scalar(
        select(Story).where(
            Story.creator_id == creator.id,
            Story.idempotency_key == normalized_key,
        )
    )
    if existing:
        if (
            existing.media_asset_id != media_asset_id
            or existing.caption != caption
            or existing.alt_text != alt_text
            or existing.access_policy is not access_policy
        ):
            raise ValueError("Idempotency-Key was already used for a different Story")
        return existing
    if access_policy not in SUPPORTED_ACCESS_POLICIES:
        raise ValueError("Stories support only free, followers, or subscription access")
    asset = await db.get(MediaAsset, media_asset_id)
    if not asset or asset.owner_creator_id != creator.id:
        raise PermissionError("Media asset not found")
    if (
        asset.status is not MediaStatus.ready
        or asset.deleted_at is not None
        or asset.moderation_status in UNSAFE_MODERATION_STATUSES
    ):
        raise ValueError("Story media must be ready and eligible for delivery")
    if not await delivery_derivative(db, asset):
        kind = required_derivative_type(asset.media_type).value
        raise ValueError(f"Story media requires a ready {kind} derivative")
    if not await asset_is_story_safe(db, asset.id):
        raise ValueError(
            "Story media must not be shared with content, posts, messages, or marketplace listings"
        )
    published_at = now or datetime.now(UTC)
    if published_at.tzinfo is None:
        raise ValueError("Story publication time must include a timezone")
    story = Story(
        creator_id=creator.id,
        created_by_user_id=user.id,
        media_asset_id=asset.id,
        idempotency_key=normalized_key,
        status=StoryStatus.active,
        access_policy=access_policy,
        caption=caption,
        alt_text=alt_text,
        created_at=published_at,
        updated_at=published_at,
        published_at=published_at,
        expires_at=published_at + STORY_LIFETIME,
    )
    db.add(story)
    await db.flush()
    await record_event(
        db,
        "story.created",
        actor_user_id=user.id,
        target_type="story",
        target_id=str(story.id),
        metadata={"access_policy": story.access_policy.value},
    )
    return story


async def own_story(
    db: AsyncSession,
    user: User,
    story_id: UUID,
    *,
    for_update: bool = False,
) -> Story:
    creator = await approved_creator(db, user)
    query = select(Story).where(Story.id == story_id, Story.creator_id == creator.id)
    if for_update:
        query = query.with_for_update()
    story = await db.scalar(query)
    if not story:
        raise PermissionError("Story not found")
    return story


async def delete_story(
    db: AsyncSession, user: User, story_id: UUID, *, now: datetime | None = None
) -> Story:
    """Soft-delete idempotently so moderation and lifecycle history remain durable."""
    story = await own_story(db, user, story_id, for_update=True)
    if story.status in {StoryStatus.deleted, StoryStatus.removed}:
        return story
    story.status = StoryStatus.deleted
    story.deleted_at = now or datetime.now(UTC)
    await record_event(
        db,
        "story.deleted",
        actor_user_id=user.id,
        target_type="story",
        target_id=str(story.id),
    )
    return story


async def remove_story_for_moderation(
    db: AsyncSession,
    story_id: UUID,
    actor: User,
    *,
    now: datetime | None = None,
) -> Story:
    story = await db.scalar(select(Story).where(Story.id == story_id).with_for_update())
    if not story:
        raise ValueError("Story not found")
    if story.status in {StoryStatus.removed, StoryStatus.deleted}:
        return story
    story.status = StoryStatus.removed
    story.removed_at = now or datetime.now(UTC)
    await record_event(
        db,
        "story.removed",
        actor_user_id=actor.id,
        target_type="story",
        target_id=str(story.id),
    )
    return story


async def expire_due_stories(db: AsyncSession, *, now: datetime | None = None) -> int:
    """Expire each due active Story once; row locks make concurrent sweeps replay-safe."""
    current_time = now or datetime.now(UTC)
    stories = (
        await db.scalars(
            select(Story)
            .where(Story.status == StoryStatus.active, Story.expires_at <= current_time)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for story in stories:
        story.status = StoryStatus.expired
        story.expired_at = current_time
        await record_event(
            db,
            "story.expired",
            target_type="story",
            target_id=str(story.id),
        )
    return len(stories)


def _active_public_query(
    user: User | None,
    current_time: datetime,
    access_decision: AdultAccessDecision | None = None,
):
    decision = access_decision or resolve_adult_access(user, None, now=current_time)
    ready_derivative = exists(
        select(MediaDerivative.id).where(
            MediaDerivative.media_asset_id == Story.media_asset_id,
            MediaDerivative.status == MediaStatus.ready,
            or_(
                and_(
                    MediaAsset.media_type == MediaType.image,
                    MediaDerivative.derivative_type == DerivativeType.display,
                ),
                and_(
                    MediaAsset.media_type == MediaType.video,
                    MediaDerivative.derivative_type == DerivativeType.preview_clip,
                ),
            ),
        )
    )
    access = Story.access_policy == AccessPolicy.free
    if user:
        owner_access = CreatorProfile.user_id == user.id
        follower_access = and_(
            Story.access_policy == AccessPolicy.followers,
            exists(
                select(Follow.id).where(
                    Follow.user_id == user.id,
                    Follow.creator_id == Story.creator_id,
                )
            ),
        )
        subscription_access = and_(
            Story.access_policy == AccessPolicy.subscription,
            exists(
                select(ContentEntitlement.id).where(
                    ContentEntitlement.subject_user_id == user.id,
                    ContentEntitlement.creator_id == Story.creator_id,
                    ContentEntitlement.status == EntitlementStatus.active,
                    ContentEntitlement.valid_from <= current_time,
                    or_(
                        ContentEntitlement.valid_until.is_(None),
                        ContentEntitlement.valid_until > current_time,
                    ),
                )
            ),
        )
        if {role.name for role in user.roles} & {"admin", "moderator", "super_admin"}:
            access = True
        else:
            access = or_(access, owner_access, follower_access, subscription_access)
        blocked = exists(
            select(UserBlock.id).where(
                or_(
                    and_(
                        UserBlock.blocker_user_id == user.id,
                        UserBlock.blocked_user_id == CreatorProfile.user_id,
                    ),
                    and_(
                        UserBlock.blocked_user_id == user.id,
                        UserBlock.blocker_user_id == CreatorProfile.user_id,
                    ),
                )
            )
        )
    else:
        blocked = False
    return (
        select(Story)
        .join(CreatorProfile, CreatorProfile.id == Story.creator_id)
        .join(MediaAsset, MediaAsset.id == Story.media_asset_id)
        .where(
            Story.status == StoryStatus.active,
            Story.expires_at > current_time,
            CreatorProfile.status == CreatorStatus.approved,
            CreatorProfile.is_public.is_(True),
            CreatorProfile.username.is_not(None),
            current_adult_verification_predicate(Story.creator_id),
            MediaAsset.owner_creator_id == Story.creator_id,
            MediaAsset.status == MediaStatus.ready,
            MediaAsset.deleted_at.is_(None),
            MediaAsset.moderation_status.notin_(UNSAFE_MODERATION_STATUSES),
            ready_derivative,
            ~external_asset_reference(Story.media_asset_id),
            True if decision.allowed else MediaAsset.audience == MediaAudience.safe_public,
            ~blocked if user else True,
            access,
        )
    )


def encode_cursor(story: Story) -> str:
    return f"{story.published_at.isoformat()}|{story.id}"


def parse_cursor(value: str | None) -> tuple[datetime, UUID] | None:
    if not value:
        return None
    try:
        published_at, story_id = value.rsplit("|", 1)
        timestamp = datetime.fromisoformat(published_at)
        if timestamp.tzinfo is None:
            raise ValueError
        return timestamp, UUID(story_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid Story cursor") from exc


async def public_rail(
    db: AsyncSession,
    user: User | None,
    cursor: str | None,
    limit: int,
    creator_username: str | None = None,
    access_decision: AdultAccessDecision | None = None,
    *,
    now: datetime | None = None,
) -> tuple[list[Story], str | None]:
    current_time = now or datetime.now(UTC)
    query = _active_public_query(user, current_time, access_decision)
    if creator_username:
        query = query.where(CreatorProfile.username == creator_username.strip().lower())
    parsed = parse_cursor(cursor)
    if parsed:
        published_at, story_id = parsed
        query = query.where(
            or_(
                Story.published_at < published_at,
                and_(Story.published_at == published_at, Story.id < story_id),
            )
        )
    rows = (
        await db.scalars(
            query.order_by(Story.published_at.desc(), Story.id.desc()).limit(limit + 1)
        )
    ).all()
    page = rows[:limit]
    return page, encode_cursor(page[-1]) if len(rows) > limit and page else None


async def public_story(
    db: AsyncSession,
    story_id: UUID,
    user: User | None,
    *,
    now: datetime | None = None,
    access_decision: AdultAccessDecision | None = None,
) -> Story | None:
    current_time = now or datetime.now(UTC)
    return await db.scalar(
        _active_public_query(user, current_time, access_decision).where(Story.id == story_id)
    )


async def own_stories(
    db: AsyncSession,
    user: User,
    status: StoryStatus | None,
    limit: int,
) -> list[Story]:
    creator = await approved_creator(db, user)
    query = select(Story).where(Story.creator_id == creator.id)
    if status:
        query = query.where(Story.status == status)
    return (
        await db.scalars(query.order_by(Story.created_at.desc(), Story.id.desc()).limit(limit))
    ).all()
