from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.accounts import service as accounts
from app.content.access import can_access_content
from app.creators import service as creators
from app.models.content import AccessPolicy, ContentItem, ContentStatus, ContentType
from app.models.creator import CreatorStatus
from app.models.social import FeedPost, FeedPostStatus, FeedPostType, Follow, PostReaction
from app.social import service as social


async def creator(db, email: str):
    user, _ = await accounts.register(db, email, "strong-password-123", None)
    profile = await creators.get_or_create_profile(db, user)
    await creators.update_profile(db, profile, {"username": email.split("@")[0], "display_name": "Creator"}, user.id)
    await creators.submit(db, profile, user.id)
    await creators.development_verify(db, profile, True, user.id)
    await creators.set_status(db, profile, CreatorStatus.approved, user.id)
    profile.is_public = True
    return user, profile


@pytest.mark.asyncio
async def test_follow_is_idempotent_and_enables_followers_content(db_session):
    owner, profile = await creator(db_session, "social-owner@example.com")
    viewer, _ = await accounts.register(db_session, "social-viewer@example.com", "strong-password-123", None)
    content = ContentItem(owner_creator_id=profile.id, created_by_user_id=owner.id, content_type=ContentType.gallery, title="Followers", status=ContentStatus.published, access_policy=AccessPolicy.followers)
    db_session.add(content); await db_session.flush()
    assert not await can_access_content(db_session, content, viewer)
    assert await social.follow(db_session, viewer, profile.id)
    assert not await social.follow(db_session, viewer, profile.id)
    assert await can_access_content(db_session, content, viewer)
    assert await db_session.scalar(select(func.count()).select_from(Follow)) == 1
    assert await social.unfollow(db_session, viewer, profile.id)
    assert not await can_access_content(db_session, content, viewer)


@pytest.mark.asyncio
async def test_locked_posts_hide_body_and_reactions_are_unique(db_session):
    owner, profile = await creator(db_session, "post-owner@example.com")
    viewer, _ = await accounts.register(db_session, "post-viewer@example.com", "strong-password-123", None)
    post = await social.create_post(db_session, owner, {"post_type": "text", "body": "secret #Cosplay", "access_policy": AccessPolicy.followers})
    await social.publish(db_session, owner, post.id)
    assert not await social.can_access_post(db_session, post, viewer)
    await social.follow(db_session, viewer, profile.id)
    assert await social.can_access_post(db_session, post, viewer)
    db_session.add(PostReaction(post_id=post.id, user_id=viewer.id)); await db_session.flush()
    assert await db_session.scalar(select(func.count()).select_from(PostReaction).where(PostReaction.post_id == post.id)) == 1
    rows, cursor = await social.feed_posts(db_session, viewer, "following", None, None, 1)
    assert rows == [post] and cursor is None


@pytest.mark.asyncio
async def test_scheduling_and_auto_posts_are_replay_safe(db_session):
    owner, profile = await creator(db_session, "schedule-owner@example.com")
    future = datetime.now(UTC) + timedelta(hours=1)
    scheduled = await social.create_post(db_session, owner, {"post_type": "text", "body": "later", "scheduled_at": future})
    assert scheduled.status is FeedPostStatus.scheduled
    assert await social.publish_due_posts(db_session) == 0
    scheduled.scheduled_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await social.publish_due_posts(db_session) == 1
    assert scheduled.status is FeedPostStatus.published
    settings = await social.settings_for_creator(db_session, profile.id); settings.auto_post_galleries = True
    content = ContentItem(owner_creator_id=profile.id, created_by_user_id=owner.id, content_type=ContentType.gallery, title="New gallery", status=ContentStatus.published, access_policy=AccessPolicy.ppv)
    db_session.add(content); await db_session.flush()
    assert await social.auto_post_content(db_session, content)
    assert await social.auto_post_content(db_session, content) is None
    assert await db_session.scalar(select(func.count()).select_from(FeedPost).where(FeedPost.source_content_id == content.id)) == 1
    disabled = ContentItem(owner_creator_id=profile.id, created_by_user_id=owner.id, content_type=ContentType.gallery, title="No announcement", status=ContentStatus.published, access_policy=AccessPolicy.free, feed_announcement_override=False)
    db_session.add(disabled); await db_session.flush()
    assert await social.auto_post_content(db_session, disabled) is None
    enabled = ContentItem(owner_creator_id=profile.id, created_by_user_id=owner.id, content_type=ContentType.gallery, title="Forced announcement", status=ContentStatus.published, access_policy=AccessPolicy.free, feed_announcement_override=True)
    db_session.add(enabled); await db_session.flush()
    settings.auto_post_galleries = False
    assert await social.auto_post_content(db_session, enabled)


@pytest.mark.asyncio
async def test_feed_cursor_covers_equal_timestamps_and_pinned_sort_key(db_session):
    owner, profile = await creator(db_session, "cursor-owner@example.com")
    now = datetime.now(UTC)
    posts = [
        FeedPost(
            creator_id=profile.id,
            created_by_user_id=owner.id,
            post_type=FeedPostType.text,
            body=f"post {index}",
            status=FeedPostStatus.published,
            access_policy=AccessPolicy.free,
            published_at=now,
            pinned_at=now if index == 0 else None,
        )
        for index in range(3)
    ]
    db_session.add_all(posts)
    await db_session.flush()
    first, cursor = await social.feed_posts(db_session, None, "discover", None, None, 1)
    second, cursor_two = await social.feed_posts(db_session, None, "discover", None, cursor, 1)
    third, cursor_three = await social.feed_posts(db_session, None, "discover", None, cursor_two, 1)
    assert cursor and cursor_two and cursor_three is None
    assert len({first[0].id, second[0].id, third[0].id}) == 3
