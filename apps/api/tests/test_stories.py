import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.accounts import service as accounts
from app.api.routes import stories as story_routes
from app.creators import service as creators
from app.main import app
from app.models.audit import AuditEvent
from app.models.content import (
    AccessPolicy,
    ContentEntitlement,
    ContentItem,
    ContentStatus,
    ContentType,
    DerivativeType,
    Gallery,
    GalleryItem,
    MediaAsset,
    MediaAudience,
    MediaDerivative,
    MediaStatus,
    MediaType,
    ModerationStatus,
)
from app.models.creator import CreatorStatus, CreatorVerification, VerificationStatus
from app.models.messaging import UserBlock
from app.models.social import Follow
from app.models.story import StoryStatus
from app.stories import service as stories
from app.worker.celery_app import celery_app

PASSWORD = "strong-password-123"


async def creator(db, email: str, username: str):
    user, _ = await accounts.register(db, email, PASSWORD, None)
    profile = await creators.get_or_create_profile(db, user)
    await creators.update_profile(
        db,
        profile,
        {"username": username, "display_name": username.replace("-", " ").title()},
        user.id,
    )
    await creators.submit(db, profile, user.id)
    await creators.development_verify(db, profile, True, user.id)
    await creators.set_status(db, profile, CreatorStatus.approved, user.id)
    profile.is_public = True
    return user, profile


async def ready_asset(
    db,
    creator_id,
    name: str,
    media_type: MediaType = MediaType.image,
    *,
    moderation_status: ModerationStatus = ModerationStatus.not_reviewed,
    with_derivative: bool = True,
    audience: MediaAudience = MediaAudience.safe_public,
):
    mime_type = "image/png" if media_type is MediaType.image else "video/mp4"
    asset = MediaAsset(
        owner_creator_id=creator_id,
        media_type=media_type,
        status=MediaStatus.ready,
        moderation_status=moderation_status,
        audience=audience,
        storage_key=f"original/private-{name}",
        original_filename=f"{name}.{'png' if media_type is MediaType.image else 'mp4'}",
        mime_type=mime_type,
    )
    db.add(asset)
    await db.flush()
    derivative = None
    if with_derivative:
        derivative_type = (
            DerivativeType.display if media_type is MediaType.image else DerivativeType.preview_clip
        )
        derivative = MediaDerivative(
            media_asset_id=asset.id,
            derivative_type=derivative_type,
            status=MediaStatus.ready,
            storage_key=f"derivative/{name}-{derivative_type.value}",
            mime_type="image/webp" if media_type is MediaType.image else "video/mp4",
        )
        db.add(derivative)
        await db.flush()
    return asset, derivative


async def publish_story(
    db,
    user,
    asset_id,
    caption,
    alt_text,
    access_policy,
    *,
    now=None,
    idempotency_key: str | None = None,
):
    return await stories.create_story(
        db,
        user,
        asset_id,
        caption,
        alt_text,
        access_policy,
        idempotency_key or f"test-story-{uuid4()}",
        now=now,
    )


@pytest.mark.asyncio
async def test_story_creation_enforces_owned_safe_derivative_and_exact_lifecycle(db_session):
    owner, profile = await creator(db_session, "story-owner@example.com", "story-owner")
    other, other_profile = await creator(db_session, "story-other@example.com", "story-other")
    fan, _ = await accounts.register(db_session, "story-fan@example.com", PASSWORD, None)
    asset, derivative = await ready_asset(db_session, profile.id, "portrait")
    other_asset, _ = await ready_asset(db_session, other_profile.id, "other")
    no_derivative, _ = await ready_asset(
        db_session, profile.id, "unfinished", with_derivative=False
    )
    flagged, _ = await ready_asset(
        db_session,
        profile.id,
        "flagged",
        moderation_status=ModerationStatus.flagged,
    )
    now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)

    story = await publish_story(
        db_session,
        owner,
        asset.id,
        "A safe caption",
        "Portrait at sunset",
        AccessPolicy.free,
        now=now,
        idempotency_key="story-owner-create-one",
    )
    assert story.status is StoryStatus.active
    assert story.created_at == story.published_at == now
    assert story.expires_at == now + timedelta(hours=24)
    assert await stories.delivery_derivative(db_session, asset) == derivative
    replay = await publish_story(
        db_session,
        owner,
        asset.id,
        "A safe caption",
        "Portrait at sunset",
        AccessPolicy.free,
        now=now + timedelta(minutes=1),
        idempotency_key="story-owner-create-one",
    )
    assert replay.id == story.id
    with pytest.raises(ValueError, match="different Story"):
        await publish_story(
            db_session,
            owner,
            asset.id,
            "Changed retry payload",
            "Portrait at sunset",
            AccessPolicy.free,
            idempotency_key="story-owner-create-one",
        )

    with pytest.raises(PermissionError, match="Media asset not found"):
        await publish_story(
            db_session, owner, other_asset.id, None, None, AccessPolicy.free, now=now
        )
    with pytest.raises(PermissionError, match="approved creator profile"):
        await publish_story(db_session, fan, asset.id, None, None, AccessPolicy.free, now=now)
    with pytest.raises(ValueError, match="ready display derivative"):
        await publish_story(
            db_session, owner, no_derivative.id, None, None, AccessPolicy.free, now=now
        )
    with pytest.raises(ValueError, match="eligible for delivery"):
        await publish_story(db_session, owner, flagged.id, None, None, AccessPolicy.free, now=now)
    with pytest.raises(ValueError, match="only free, followers, or subscription"):
        await publish_story(db_session, owner, asset.id, None, None, AccessPolicy.ppv, now=now)

    shared_asset, _ = await ready_asset(db_session, profile.id, "restricted-shared")
    restricted = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=999,
        price_currency="EUR",
        title="Restricted gallery",
    )
    restricted.gallery = Gallery(
        items=[GalleryItem(media_asset_id=shared_asset.id, position=0, is_preview=False)]
    )
    db_session.add(restricted)
    await db_session.flush()
    with pytest.raises(ValueError, match="must not be shared"):
        await publish_story(
            db_session,
            owner,
            shared_asset.id,
            None,
            None,
            AccessPolicy.free,
        )

    story.media_asset_id = other_asset.id
    await db_session.flush()
    assert (
        await stories.public_story(
            db_session,
            story.id,
            None,
            now=now + timedelta(hours=1),
        )
        is None
    )
    with pytest.raises(HTTPException) as missing_projection:
        await story_routes.story_response(db_session, story)
    assert missing_projection.value.status_code == 404
    assert other.id != owner.id


@pytest.mark.asyncio
async def test_video_story_selects_preview_clip_and_never_full_playback(db_session):
    owner, profile = await creator(db_session, "video-story@example.com", "video-story")
    asset, preview = await ready_asset(db_session, profile.id, "short-video", MediaType.video)
    playback = MediaDerivative(
        media_asset_id=asset.id,
        derivative_type=DerivativeType.playback,
        status=MediaStatus.ready,
        storage_key="derivative/full-playback-must-not-serve",
        mime_type="video/mp4",
    )
    db_session.add(playback)
    await db_session.flush()

    story = await publish_story(db_session, owner, asset.id, None, None, AccessPolicy.free)
    selected = await stories.delivery_derivative(db_session, asset)
    assert selected and preview and selected.id == preview.id
    payload = (await story_routes.story_response(db_session, story)).model_dump(mode="json")
    assert payload["media"]["derivative_id"] == str(preview.id)
    assert payload["creator"]["verified"] is True
    assert "full-playback-must-not-serve" not in str(payload)
    assert asset.storage_key not in str(payload)

    verification = await db_session.scalar(
        select(CreatorVerification).where(CreatorVerification.creator_profile_id == profile.id)
    )
    assert verification
    verification.adult_verified = False
    unverified_payload = (await story_routes.story_response(db_session, story)).model_dump(
        mode="json"
    )
    assert unverified_payload["creator"]["verified"] is False


@pytest.mark.asyncio
async def test_public_rail_resolves_access_and_excludes_due_or_unsafe_rows(db_session):
    owner, profile = await creator(db_session, "rail-owner@example.com", "rail-owner")
    viewer, _ = await accounts.register(db_session, "rail-viewer@example.com", PASSWORD, None)
    asset, _ = await ready_asset(db_session, profile.id, "rail")
    now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    free = await publish_story(
        db_session, owner, asset.id, "free", None, AccessPolicy.free, now=now
    )
    follower = await publish_story(
        db_session,
        owner,
        asset.id,
        "followers",
        None,
        AccessPolicy.followers,
        now=now + timedelta(seconds=1),
    )
    subscription = await publish_story(
        db_session,
        owner,
        asset.id,
        "subscription",
        None,
        AccessPolicy.subscription,
        now=now + timedelta(seconds=2),
    )
    overdue = await publish_story(
        db_session,
        owner,
        asset.id,
        "overdue",
        None,
        AccessPolicy.free,
        now=now - timedelta(days=2),
    )
    current_time = now + timedelta(hours=1)

    anonymous, _ = await stories.public_rail(db_session, None, None, 50, now=current_time)
    assert anonymous == [free]
    filtered, _ = await stories.public_rail(
        db_session, None, None, 50, "RAIL-OWNER", now=current_time
    )
    assert filtered == [free]
    missing, _ = await stories.public_rail(
        db_session, None, None, 50, "missing-creator", now=current_time
    )
    assert missing == []
    owner_rows, _ = await stories.public_rail(db_session, owner, None, 50, now=current_time)
    assert owner_rows == [subscription, follower, free]
    viewer_rows, _ = await stories.public_rail(db_session, viewer, None, 50, now=current_time)
    assert viewer_rows == [free]

    db_session.add(Follow(user_id=viewer.id, creator_id=profile.id))
    db_session.add(
        ContentEntitlement(
            subject_user_id=viewer.id,
            creator_id=profile.id,
            source_type="subscription",
            valid_from=now,
            valid_until=now + timedelta(days=1),
        )
    )
    await db_session.flush()
    accessible, _ = await stories.public_rail(db_session, viewer, None, 50, now=current_time)
    assert accessible == [subscription, follower, free]

    block = UserBlock(blocker_user_id=viewer.id, blocked_user_id=owner.id)
    db_session.add(block)
    await db_session.flush()
    assert (await stories.public_rail(db_session, viewer, None, 50, now=current_time))[0] == []
    await db_session.delete(block)
    await db_session.flush()
    reverse_block = UserBlock(blocker_user_id=owner.id, blocked_user_id=viewer.id)
    db_session.add(reverse_block)
    await db_session.flush()
    assert (await stories.public_rail(db_session, viewer, None, 50, now=current_time))[0] == []
    await db_session.delete(reverse_block)
    await db_session.flush()

    linked_content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.free,
        title="Later linked gallery",
    )
    linked_content.gallery = Gallery(
        items=[GalleryItem(media_asset_id=asset.id, position=0, is_preview=True)]
    )
    db_session.add(linked_content)
    await db_session.flush()
    assert (await stories.public_rail(db_session, viewer, None, 50, now=current_time))[0] == []
    await db_session.delete(linked_content)
    await db_session.flush()

    asset.moderation_status = ModerationStatus.rejected
    assert (await stories.public_rail(db_session, viewer, None, 50, now=current_time))[0] == []
    assert overdue.status is StoryStatus.active
    asset.moderation_status = ModerationStatus.approved
    db_session.add(
        CreatorVerification(
            creator_profile_id=profile.id,
            provider="test-expiry",
            provider_reference="test-story-current-verification-expired",
            status=VerificationStatus.expired,
            adult_verified=False,
            created_at=now + timedelta(days=10),
        )
    )
    await db_session.flush()
    assert (await stories.public_rail(db_session, viewer, None, 50, now=current_time))[0] == []


@pytest.mark.asyncio
async def test_expiry_and_delete_transitions_are_replay_safe(db_session):
    owner, profile = await creator(db_session, "expiry-owner@example.com", "expiry-owner")
    asset, _ = await ready_asset(db_session, profile.id, "expiry")
    published_at = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    expired = await publish_story(
        db_session,
        owner,
        asset.id,
        None,
        None,
        AccessPolicy.free,
        now=published_at,
    )
    active = await publish_story(
        db_session,
        owner,
        asset.id,
        None,
        None,
        AccessPolicy.free,
        now=published_at + timedelta(days=2),
    )
    sweep_time = published_at + timedelta(days=1, seconds=1)
    assert await stories.expire_due_stories(db_session, now=sweep_time) == 1
    assert await stories.expire_due_stories(db_session, now=sweep_time) == 0
    assert expired.status is StoryStatus.expired
    assert expired.expired_at == sweep_time
    assert active.status is StoryStatus.active

    deleted = await stories.delete_story(db_session, owner, expired.id, now=sweep_time)
    deleted_at = deleted.deleted_at
    assert deleted.status is StoryStatus.deleted
    assert deleted.expired_at == sweep_time
    assert await stories.delete_story(db_session, owner, expired.id) is deleted
    assert deleted.deleted_at == deleted_at
    assert celery_app.conf.task_routes["app.worker.tasks.expire_stories"] == {"queue": "scheduled"}
    assert celery_app.conf.beat_schedule["story-expiry"] == {
        "task": "app.worker.tasks.expire_stories",
        "schedule": 60.0,
    }
    assert (
        stories.public_delivery_ttl(
            active,
            300,
            now=active.expires_at - timedelta(milliseconds=1_800),
        )
        == 1
    )
    with pytest.raises(ValueError, match="expired"):
        stories.public_delivery_ttl(
            active,
            300,
            now=active.expires_at - timedelta(milliseconds=800),
        )


@pytest.mark.asyncio
async def test_story_report_and_moderation_removal_are_authoritative(db_session):
    owner, profile = await creator(db_session, "report-owner@example.com", "report-owner")
    reporter, _ = await accounts.register(db_session, "story-reporter@example.com", PASSWORD, None)
    moderator, _ = await accounts.register(
        db_session, "story-moderator@example.com", PASSWORD, None
    )
    await accounts.assign_role(db_session, moderator, "moderator", moderator.id, None)
    asset, _ = await ready_asset(db_session, profile.id, "reported-story")
    story = await publish_story(
        db_session,
        owner,
        asset.id,
        "A reportable Story caption",
        None,
        AccessPolicy.free,
    )
    dismissed_story = await publish_story(
        db_session,
        owner,
        asset.id,
        "A Story whose report is dismissed",
        None,
        AccessPolicy.free,
    )
    verified_at = accounts._now()
    for user in (owner, reporter, moderator):
        user.email_verified_at = verified_at
    await db_session.commit()

    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as reporter_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as moderator_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as anonymous_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as owner_client,
    ):
        assert (
            await reporter_client.post(
                "/api/v1/auth/login",
                json={"email": reporter.email, "password": PASSWORD},
            )
        ).status_code == 200
        report_path = f"/api/v1/feed/reports/story/{story.id}"
        duplicate_reports = await asyncio.gather(
            reporter_client.post(
                report_path,
                json={"reason": "story_safety_concern"},
            ),
            reporter_client.post(
                report_path,
                json={"reason": "story_safety_concern"},
            ),
        )
        assert [response.status_code for response in duplicate_reports] == [200, 200]
        dismissed_report_response = await reporter_client.post(
            f"/api/v1/feed/reports/story/{dismissed_story.id}",
            json={"reason": "story_safety_concern"},
        )
        assert dismissed_report_response.status_code == 200

        assert (
            await moderator_client.post(
                "/api/v1/auth/login",
                json={"email": moderator.email, "password": PASSWORD},
            )
        ).status_code == 200
        reports = await moderator_client.get("/api/v1/admin/social-reports")
        assert reports.status_code == 200
        matching = [
            item
            for item in reports.json()
            if item["target_type"] == "story" and item["target_id"] == str(story.id)
        ]
        assert len(matching) == 1
        assert matching[0]["target_preview"] == "A reportable Story caption"
        dismissed_matching = [
            item
            for item in reports.json()
            if item["target_type"] == "story" and item["target_id"] == str(dismissed_story.id)
        ]
        assert len(dismissed_matching) == 1
        assert (
            await moderator_client.post(
                f"/api/v1/admin/social-reports/{dismissed_matching[0]['id']}/dismiss"
            )
        ).status_code == 200
        cannot_remove = await moderator_client.post(
            f"/api/v1/admin/social-reports/{dismissed_matching[0]['id']}/remove-target"
        )
        assert cannot_remove.status_code == 409
        removed = await moderator_client.post(
            f"/api/v1/admin/social-reports/{matching[0]['id']}/remove-target"
        )
        assert removed.status_code == 200
        repeated_removal = await moderator_client.post(
            f"/api/v1/admin/social-reports/{matching[0]['id']}/remove-target"
        )
        assert repeated_removal.status_code == 200
        assert (await anonymous_client.get(f"/api/v1/stories/{story.id}")).status_code == 404
        assert (
            await anonymous_client.get(f"/api/v1/stories/{dismissed_story.id}")
        ).status_code == 200
        assert (
            await owner_client.post(
                "/api/v1/auth/login",
                json={"email": owner.email, "password": PASSWORD},
            )
        ).status_code == 200
        assert (
            await owner_client.get(f"/api/v1/stories/{story.id}/media", follow_redirects=False)
        ).status_code == 404

    await db_session.refresh(story)
    assert story.status is StoryStatus.removed
    assert story.removed_at is not None
    removal_audits = await db_session.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(
            AuditEvent.event_type == "story.removed",
            AuditEvent.target_id == str(story.id),
        )
    )
    assert removal_audits == 1


@pytest.mark.asyncio
async def test_story_api_projects_only_safe_paths_and_soft_deleted_detail_disappears(
    db_session, monkeypatch
):
    owner, profile = await creator(db_session, "story-api@example.com", "story-api")
    asset, derivative = await ready_asset(db_session, profile.id, "api-safe")
    historical = await publish_story(
        db_session,
        owner,
        asset.id,
        "Historical Story",
        None,
        AccessPolicy.free,
        now=datetime.now(UTC) - timedelta(days=2),
    )
    owner.email_verified_at = accounts._now()
    await db_session.commit()

    class FakeStorage:
        def create_download_url(self, key: str, expires_in: int) -> str:
            assert derivative and key == derivative.storage_key
            assert expires_in > 0
            return f"https://cdn.invalid/{key}"

    monkeypatch.setattr(story_routes, "storage_provider", lambda: FakeStorage())
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as creator_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as anonymous_client,
    ):
        login = await creator_client.post(
            "/api/v1/auth/login",
            json={"email": owner.email, "password": PASSWORD},
        )
        assert login.status_code == 200
        expired = await creator_client.get("/api/v1/stories/mine?status=expired")
        assert expired.status_code == 200
        assert [item["id"] for item in expired.json()] == [str(historical.id)]
        created = await creator_client.post(
            "/api/v1/stories",
            headers={"Idempotency-Key": "story-api-safe-create"},
            json={
                "media_asset_id": str(asset.id),
                "caption": "Safe projection",
                "alt_text": "A creator portrait",
                "access_policy": "free",
            },
        )
        assert created.status_code == 201
        payload = created.json()
        story_id = payload["id"]
        assert payload["created_at"]
        assert payload["media"]["delivery_path"] == f"/stories/{story_id}/media"
        assert asset.storage_key not in created.text
        assert derivative and derivative.storage_key not in created.text

        replayed = await creator_client.post(
            "/api/v1/stories",
            headers={"Idempotency-Key": "story-api-safe-create"},
            json={
                "media_asset_id": str(asset.id),
                "caption": "Safe projection",
                "alt_text": "A creator portrait",
                "access_policy": "free",
            },
        )
        assert replayed.status_code == 201
        assert replayed.json()["id"] == story_id

        missing_key = await creator_client.post(
            "/api/v1/stories",
            json={
                "media_asset_id": str(asset.id),
                "caption": "No replay key",
                "access_policy": "free",
            },
        )
        assert missing_key.status_code == 400

        rail = await anonymous_client.get("/api/v1/stories/rail")
        detail = await anonymous_client.get(f"/api/v1/stories/{story_id}")
        assert rail.status_code == detail.status_code == 200
        assert rail.json()["items"] == [detail.json()]

        delivery = await anonymous_client.get(
            f"/api/v1/stories/{story_id}/media", follow_redirects=False
        )
        assert delivery.status_code in {302, 307}
        assert delivery.headers["location"].endswith(derivative.storage_key)
        assert asset.storage_key not in delivery.headers["location"]

        deleted = await creator_client.delete(f"/api/v1/stories/{story_id}")
        repeated = await creator_client.delete(f"/api/v1/stories/{story_id}")
        assert deleted.status_code == repeated.status_code == 200
        assert deleted.json()["status"] == repeated.json()["status"] == "deleted"
        assert (await anonymous_client.get(f"/api/v1/stories/{story_id}")).status_code == 404
        mine = await creator_client.get("/api/v1/stories/mine?status=deleted")
        assert mine.status_code == 200
        assert [item["id"] for item in mine.json()] == [story_id]
