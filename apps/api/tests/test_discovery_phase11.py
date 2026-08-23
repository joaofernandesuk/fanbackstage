from datetime import UTC, datetime

import pytest

from app.accounts import service as accounts
from app.creators import service as creators
from app.discovery import service
from app.models.content import AccessPolicy, ContentItem, ContentStatus, ContentType
from app.models.creator import CreatorStatus
from app.models.messaging import UserBlock
from app.social import service as social


async def creator(db, email: str):
    user, _ = await accounts.register(db, email, "strong-password-123", None)
    profile = await creators.get_or_create_profile(db, user)
    await creators.update_profile(
        db,
        profile,
        {
            "username": email.split("@")[0],
            "display_name": email.split("@")[0].title(),
            "bio": "Public creator",
        },
        user.id,
    )
    await creators.submit(db, profile, user.id)
    await creators.development_verify(db, profile, True, user.id)
    await creators.set_status(db, profile, CreatorStatus.approved, user.id)
    profile.is_public = True
    return user, profile


@pytest.mark.asyncio
async def test_discovery_filters_pending_blocked_and_locked_content(db_session):
    viewer, _ = await accounts.register(
        db_session, "viewer@example.com", "strong-password-123", None
    )
    owner, profile = await creator(db_session, "alexandra@example.com")
    blocked_owner, blocked_profile = await creator(db_session, "blocked@example.com")
    pending, _ = await accounts.register(
        db_session, "pending@example.com", "strong-password-123", None
    )
    pending_profile = await creators.get_or_create_profile(db_session, pending)
    pending_profile.username, pending_profile.display_name, pending_profile.is_public = (
        "pendingalex",
        "Pending Alex",
        True,
    )
    db_session.add(UserBlock(blocker_user_id=viewer.id, blocked_user_id=blocked_owner.id))
    locked = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Alexandra private gallery",
        description="Safe description",
        status=ContentStatus.published,
        access_policy=AccessPolicy.ppv,
        published_at=datetime.now(UTC),
    )
    db_session.add(locked)
    await db_session.flush()
    items, _, _ = await service.search(
        db_session, viewer, query="alex", entity_types={"creator", "gallery"}
    )
    assert {item.id for item in items} == {profile.id, locked.id}
    gallery = next(item for item in items if item.id == locked.id)
    assert gallery.locked and gallery.preview_asset_id is None
    assert blocked_profile.id not in {item.id for item in items}
    assert pending_profile.id not in {item.id for item in items}


@pytest.mark.asyncio
async def test_discovery_cursor_is_signed_stable_and_config_versioned(db_session):
    _, first = await creator(db_session, "alpha@example.com")
    _, second = await creator(db_session, "alphabet@example.com")
    items, cursor, version = await service.search(
        db_session, None, query="alp", entity_types={"creator"}, limit=1
    )
    assert cursor and version == 1
    next_items, _, _ = await service.search(
        db_session, None, query="alp", entity_types={"creator"}, cursor=cursor, limit=1
    )
    assert {items[0].id, next_items[0].id} == {first.id, second.id}
    with pytest.raises(ValueError, match="Invalid discovery cursor"):
        await service.search(
            db_session, None, query="alp", entity_types={"creator"}, cursor=cursor[:-1] + "x"
        )
    admin, _ = await accounts.register(
        db_session, "admin-discovery@example.com", "strong-password-123", None
    )
    config = await service.update_config(
        db_session,
        admin,
        {
            "text_weight": 99,
            "live_boost": 40,
            "recency_weight": 20,
            "engagement_weight": 10,
            "trending_window_hours": 24,
            "default_result_limit": 20,
        },
    )
    assert config.version == 2
    with pytest.raises(ValueError, match="stale"):
        await service.search(
            db_session, None, query="alp", entity_types={"creator"}, cursor=cursor, limit=1
        )


@pytest.mark.asyncio
async def test_locked_post_is_a_safe_discovery_result(db_session):
    owner, _ = await creator(db_session, "post-discovery@example.com")
    viewer, _ = await accounts.register(
        db_session, "post-viewer@example.com", "strong-password-123", None
    )
    post = await social.create_post(
        db_session,
        owner,
        {
            "post_type": "text",
            "body": "private discovery phrase",
            "access_policy": AccessPolicy.followers,
        },
    )
    await social.publish(db_session, owner, post.id)
    items, _, _ = await service.search(db_session, viewer, query="phrase", entity_types={"post"})
    assert len(items) == 1 and items[0].locked and items[0].id == post.id
