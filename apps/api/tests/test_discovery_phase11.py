from datetime import UTC, datetime

import httpx
import pytest

from app.accounts import service as accounts
from app.creators import service as creators
from app.discovery import service
from app.main import app
from app.models.content import (
    AccessPolicy,
    ContentItem,
    ContentStatus,
    ContentType,
    Gallery,
    GalleryItem,
    MediaAsset,
    MediaAudience,
    MediaStatus,
    MediaType,
    ModerationStatus,
    VideoContent,
)
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
        moderation_status=ModerationStatus.approved,
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


@pytest.mark.asyncio
async def test_discovery_route_parses_repeated_entity_type_query_values(db_session):
    owner, profile = await creator(db_session, "route-filter@example.com")
    post = await social.create_post(
        db_session,
        owner,
        {"post_type": "text", "body": "route filter post", "access_policy": AccessPolicy.free},
    )
    await social.publish(db_session, owner, post.id)
    await db_session.commit()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/api/v1/discovery/search",
            params=[("types", "creator"), ("limit", "50")],
        )

    assert response.status_code == 200
    assert {item["entity_type"] for item in response.json()["items"]} == {"creator"}
    assert {item["id"] for item in response.json()["items"]} == {str(profile.id)}


@pytest.mark.asyncio
async def test_content_projection_includes_canonical_gallery_count_and_video_duration(db_session):
    owner, profile = await creator(db_session, "projection@example.com")
    gallery_assets = [
        MediaAsset(
            owner_creator_id=profile.id,
            media_type=MediaType.image,
            status=MediaStatus.ready,
            moderation_status=ModerationStatus.approved,
            audience=MediaAudience.safe_public,
            storage_key=f"original/projection-gallery-{index}",
            original_filename=f"projection-{index}.png",
            mime_type="image/png",
        )
        for index in range(3)
    ]
    video_asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.video,
        status=MediaStatus.ready,
        moderation_status=ModerationStatus.approved,
        audience=MediaAudience.safe_public,
        storage_key="original/projection-video",
        original_filename="projection.mp4",
        mime_type="video/mp4",
        duration_seconds=95,
    )
    db_session.add_all([*gallery_assets, video_asset])
    await db_session.flush()
    gallery = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Projection gallery",
        status=ContentStatus.published,
        access_policy=AccessPolicy.free,
        moderation_status=ModerationStatus.approved,
        published_at=datetime.now(UTC),
    )
    gallery.gallery = Gallery(preview_count=0)
    video = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.video,
        title="Projection video",
        status=ContentStatus.published,
        access_policy=AccessPolicy.free,
        moderation_status=ModerationStatus.approved,
        published_at=datetime.now(UTC),
    )
    video.video = VideoContent(
        source_media_asset_id=video_asset.id,
        preview_start_seconds=0,
        preview_duration_seconds=20,
    )
    db_session.add_all([gallery, video])
    await db_session.flush()
    db_session.add_all(
        [
            GalleryItem(
                gallery_id=gallery.gallery.id,
                media_asset_id=asset.id,
                position=position,
            )
            for position, asset in enumerate(gallery_assets)
        ]
    )
    await db_session.flush()

    items, _, _ = await service.search(
        db_session,
        None,
        query="projection",
        entity_types={"gallery", "video"},
    )

    by_type = {item.entity_type: item for item in items}
    assert by_type["gallery"].gallery_image_count == 3
    assert by_type["gallery"].video_duration_seconds is None
    assert by_type["video"].video_duration_seconds == 95
    assert by_type["video"].gallery_image_count is None
