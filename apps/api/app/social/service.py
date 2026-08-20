import re
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.media.service import approved_creator
from app.models.content import (
    AccessPolicy,
    ContentItem,
    ContentStatus,
    MediaAsset,
    MediaStatus,
    ModerationStatus,
)
from app.models.creator import CreatorProfile, CreatorStatus
from app.models.identity import User
from app.models.social import (
    CreatorFeedSettings,
    FeedPost,
    FeedPostMedia,
    FeedPostStatus,
    FeedPostType,
    Follow,
    Hashtag,
    PostHashtag,
    PostMention,
)

MENTION = re.compile(r"(?<![\w@])@([a-z0-9_]{3,32})", re.IGNORECASE)
HASHTAG = re.compile(r"(?<!\w)#([\w]{1,80})", re.UNICODE)


async def public_creator(db: AsyncSession, creator_id: UUID) -> CreatorProfile:
    creator = await db.get(CreatorProfile, creator_id)
    if not creator or creator.status is not CreatorStatus.approved or not creator.is_public:
        raise PermissionError("Creator not found")
    return creator


async def follow(db: AsyncSession, user: User, creator_id: UUID) -> bool:
    creator = await public_creator(db, creator_id)
    if creator.user_id == user.id:
        raise ValueError("Creators cannot follow themselves")
    existing = await db.scalar(select(Follow).where(Follow.user_id == user.id, Follow.creator_id == creator_id))
    if existing:
        return False
    db.add(Follow(user_id=user.id, creator_id=creator_id))
    await record_event(db, "follow_created", actor_user_id=user.id, target_type="creator_profile", target_id=str(creator_id))
    return True


async def unfollow(db: AsyncSession, user: User, creator_id: UUID) -> bool:
    row = await db.scalar(select(Follow).where(Follow.user_id == user.id, Follow.creator_id == creator_id))
    if not row:
        return False
    await db.delete(row)
    return True


async def settings_for_creator(db: AsyncSession, creator_id: UUID) -> CreatorFeedSettings:
    value = await db.scalar(select(CreatorFeedSettings).where(CreatorFeedSettings.creator_id == creator_id))
    if not value:
        value = CreatorFeedSettings(creator_id=creator_id)
        db.add(value)
        await db.flush()
    return value


async def _index_text(db: AsyncSession, post: FeedPost) -> None:
    if not post.body:
        return
    usernames = {item.lower() for item in MENTION.findall(post.body)}
    if usernames:
        creators = (await db.scalars(select(CreatorProfile).where(CreatorProfile.username.in_(usernames), CreatorProfile.status == CreatorStatus.approved, CreatorProfile.is_public.is_(True)))).all()
        db.add_all(PostMention(post_id=post.id, mentioned_creator_id=item.id) for item in creators)
    for tag in {item.casefold() for item in HASHTAG.findall(post.body)}:
        hashtag = await db.scalar(select(Hashtag).where(Hashtag.normalized == tag))
        if not hashtag:
            hashtag = Hashtag(normalized=tag)
            db.add(hashtag)
            await db.flush()
        db.add(PostHashtag(post_id=post.id, hashtag_id=hashtag.id))


async def create_post(db: AsyncSession, user: User, values: dict) -> FeedPost:
    creator = await approved_creator(db, user)
    scheduled_at = values.get("scheduled_at")
    status = FeedPostStatus.scheduled if scheduled_at else FeedPostStatus.draft
    if scheduled_at and scheduled_at <= datetime.now(UTC):
        raise ValueError("Scheduled publication must be in the future")
    try:
        post_type = FeedPostType(values.get("post_type", "text"))
    except ValueError as exc:
        raise ValueError("Invalid post type") from exc
    asset_ids = values.get("media_asset_ids", [])
    content_id = values.get("content_id")
    if post_type is FeedPostType.text and not values.get("body"):
        raise ValueError("Text posts require text")
    if asset_ids:
        assets = (await db.scalars(select(MediaAsset).where(MediaAsset.id.in_(asset_ids)))).all()
        if len(assets) != len(set(asset_ids)) or any(a.owner_creator_id != creator.id or a.status is not MediaStatus.ready for a in assets):
            raise ValueError("Only creator-owned ready media may be attached")
    if content_id:
        content = await db.get(ContentItem, content_id)
        if not content or content.owner_creator_id != creator.id:
            raise PermissionError("Content reference not found")
    settings = await settings_for_creator(db, creator.id)
    post = FeedPost(creator_id=creator.id, created_by_user_id=user.id, post_type=post_type, body=values.get("body"), status=status, access_policy=values.get("access_policy", AccessPolicy.free), comments_enabled=values.get("comments_enabled", settings.default_comments_enabled), reactions_enabled=values.get("reactions_enabled", True), scheduled_at=scheduled_at, source_content_id=content_id)
    db.add(post)
    await db.flush()
    db.add_all(FeedPostMedia(post_id=post.id, media_asset_id=asset_id, position=i) for i, asset_id in enumerate(asset_ids))
    await _index_text(db, post)
    return post


async def own_post(db: AsyncSession, user: User, post_id: UUID) -> FeedPost:
    creator = await approved_creator(db, user)
    post = await db.get(FeedPost, post_id)
    if not post or post.creator_id != creator.id:
        raise PermissionError("Post not found")
    return post


async def publish(db: AsyncSession, user: User, post_id: UUID) -> FeedPost:
    post = await own_post(db, user, post_id)
    if post.status not in {FeedPostStatus.draft, FeedPostStatus.scheduled}:
        raise ValueError("Only draft or scheduled posts can be published")
    post.status, post.scheduled_at, post.published_at = FeedPostStatus.published, None, datetime.now(UTC)
    await record_event(db, "post_published", actor_user_id=user.id, target_type="feed_post", target_id=str(post.id))
    return post


async def publish_due_posts(db: AsyncSession) -> int:
    now = datetime.now(UTC)
    posts = (await db.scalars(select(FeedPost).where(FeedPost.status == FeedPostStatus.scheduled, FeedPost.scheduled_at <= now).with_for_update(skip_locked=True))).all()
    for post in posts:
        post.status, post.published_at, post.scheduled_at = FeedPostStatus.published, now, None
        await record_event(db, "post_published", actor_user_id=post.created_by_user_id, target_type="feed_post", target_id=str(post.id))
    return len(posts)


async def can_access_post(db: AsyncSession, post: FeedPost, user: User | None) -> bool:
    if post.status is not FeedPostStatus.published or post.moderation_status in {ModerationStatus.flagged, ModerationStatus.rejected, ModerationStatus.removed}:
        return False
    if user:
        owner = await db.scalar(select(CreatorProfile.id).where(CreatorProfile.user_id == user.id))
        if owner == post.creator_id or {role.name for role in user.roles} & {"admin", "moderator", "super_admin"}:
            return True
    if post.access_policy is AccessPolicy.free:
        return True
    if not user:
        return False
    if post.access_policy is AccessPolicy.followers:
        return await db.scalar(select(Follow.id).where(Follow.user_id == user.id, Follow.creator_id == post.creator_id)) is not None
    if post.access_policy is AccessPolicy.subscription:
        # Reuse creator-scoped subscription entitlement via an authoritative content-shaped check.
        now = datetime.now(UTC)
        from app.models.content import ContentEntitlement, EntitlementStatus
        return await db.scalar(select(ContentEntitlement.id).where(ContentEntitlement.subject_user_id == user.id, ContentEntitlement.creator_id == post.creator_id, ContentEntitlement.status == EntitlementStatus.active, ContentEntitlement.valid_from <= now, or_(ContentEntitlement.valid_until.is_(None), ContentEntitlement.valid_until > now))) is not None
    return False


def encode_cursor(post: FeedPost) -> str:
    return f"{post.published_at.isoformat()}|{post.id}"


def parse_cursor(value: str | None) -> tuple[datetime, UUID] | None:
    if not value:
        return None
    try:
        date, identifier = value.rsplit("|", 1)
        return datetime.fromisoformat(date), UUID(identifier)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid cursor") from exc


async def feed_posts(db: AsyncSession, user: User | None, kind: str, creator_id: UUID | None, cursor: str | None, limit: int) -> tuple[list[FeedPost], str | None]:
    query = select(FeedPost).join(CreatorProfile, CreatorProfile.id == FeedPost.creator_id).where(FeedPost.status == FeedPostStatus.published, FeedPost.moderation_status.notin_([ModerationStatus.flagged, ModerationStatus.rejected, ModerationStatus.removed]), CreatorProfile.status == CreatorStatus.approved, CreatorProfile.is_public.is_(True))
    if creator_id:
        query = query.where(FeedPost.creator_id == creator_id)
    if kind == "following":
        if not user:
            raise PermissionError("Authentication required")
        query = query.join(Follow, and_(Follow.creator_id == FeedPost.creator_id, Follow.user_id == user.id))
    parsed = parse_cursor(cursor)
    if parsed:
        published, identifier = parsed
        query = query.where(or_(FeedPost.published_at < published, and_(FeedPost.published_at == published, FeedPost.id < identifier)))
    rows = (await db.scalars(query.order_by(FeedPost.pinned_at.desc().nullslast(), FeedPost.published_at.desc(), FeedPost.id.desc()).limit(limit + 1))).all()
    page, extra = rows[:limit], len(rows) > limit
    return page, encode_cursor(page[-1]) if extra and page else None


async def auto_post_content(db: AsyncSession, content: ContentItem) -> FeedPost | None:
    if content.status is not ContentStatus.published:
        return None
    settings = await settings_for_creator(db, content.owner_creator_id)
    enabled = settings.auto_post_galleries if content.content_type.value == "gallery" else settings.auto_post_videos
    if not enabled or await db.scalar(select(FeedPost.id).where(FeedPost.source_content_id == content.id)):
        return None
    post = FeedPost(creator_id=content.owner_creator_id, created_by_user_id=content.created_by_user_id, post_type=FeedPostType.gallery_reference if content.content_type.value == "gallery" else FeedPostType.video_reference, body=f"New {content.content_type.value} just dropped", status=FeedPostStatus.published, access_policy=AccessPolicy.free, published_at=content.published_at or datetime.now(UTC), source_content_id=content.id)
    db.add(post)
    await db.flush()
    return post
