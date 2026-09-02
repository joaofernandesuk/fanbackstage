from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from starlette.requests import Request

from app.accounts import service as accounts
from app.api.routes import social as social_routes
from app.api.routes import trust_safety as trust_safety_routes
from app.api.routes.social import post_response
from app.compliance.types import ComplianceDecision
from app.content.access import can_access_content
from app.core.config import Settings
from app.creators import service as creators
from app.models.compliance import AgeAssuranceLevel, ComplianceFeature
from app.models.content import (
    AccessPolicy,
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
    VideoContent,
)
from app.models.creator import CreatorStatus
from app.models.social import (
    FeedPost,
    FeedPostMedia,
    FeedPostStatus,
    FeedPostType,
    Follow,
    PostComment,
    PostCommentReaction,
    PostReaction,
    ReactionType,
)
from app.schemas.social import ReactionInput
from app.social import service as social


def decision(*, allowed: bool, feature: ComplianceFeature) -> ComplianceDecision:
    return ComplianceDecision(
        allowed=allowed,
        code="ALLOWED" if allowed else "AGE_VERIFICATION_REQUIRED",
        action=None if allowed else "VERIFY_AGE",
        reason="Policy allows access" if allowed else "Age verification is required",
        feature=feature,
        jurisdiction="PT",
        policy_id=None,
        policy_version=1,
        required_minimum_age=18,
        required_assurance_level=AgeAssuranceLevel.self_attested,
        achieved_assurance_level=(
            AgeAssuranceLevel.self_attested if allowed else AgeAssuranceLevel.none
        ),
        age_access_allowed=allowed,
        feature_allowed=True,
        country_conflict=False,
        verification_expires_at=None,
    )


@pytest.mark.asyncio
async def test_report_options_are_server_owned_and_include_other() -> None:
    payload = await trust_safety_routes.report_options()

    assert {option["value"] for option in payload["reasons"]} >= {
        "harassment",
        "non_consensual_content",
        "underage_concern",
        "other",
    }


async def creator(db, email: str):
    user, _ = await accounts.register(db, email, "strong-password-123", None)
    profile = await creators.get_or_create_profile(db, user)
    await creators.update_profile(
        db, profile, {"username": email.split("@")[0], "display_name": "Creator"}, user.id
    )
    await creators.submit(db, profile, user.id)
    await creators.development_verify(db, profile, True, user.id)
    await creators.set_status(db, profile, CreatorStatus.approved, user.id)
    profile.is_public = True
    return user, profile


@pytest.mark.asyncio
async def test_follow_is_idempotent_and_enables_followers_content(db_session):
    owner, profile = await creator(db_session, "social-owner@example.com")
    viewer, _ = await accounts.register(
        db_session, "social-viewer@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Followers",
        status=ContentStatus.published,
        access_policy=AccessPolicy.followers,
    )
    db_session.add(content)
    await db_session.flush()
    assert not await can_access_content(db_session, content, viewer)
    assert await social.follow(db_session, viewer, profile.id)
    assert not await social.follow(db_session, viewer, profile.id)
    assert await can_access_content(db_session, content, viewer)
    assert await db_session.scalar(select(func.count()).select_from(Follow)) == 1
    assert await social.unfollow(db_session, viewer, profile.id)
    assert not await can_access_content(db_session, content, viewer)


@pytest.mark.asyncio
async def test_follow_trusted_country_conflict_creates_no_relationship(db_session, monkeypatch):
    _owner, profile = await creator(db_session, "follow-conflict-owner@example.com")
    viewer, _ = await accounts.register(
        db_session, "follow-conflict-viewer@example.com", "strong-password-123", None
    )
    viewer.country_code = "PT"
    monkeypatch.setattr(
        "app.compliance.http.get_settings",
        lambda: Settings(
            environment="test",
            trusted_country_header="x-country",
            trusted_proxy_cidrs="127.0.0.1/32",
        ),
    )
    request = Request(
        {
            "type": "http",
            "client": ("127.0.0.1", 50000),
            "headers": [(b"x-country", b"GB")],
        }
    )

    with pytest.raises(HTTPException) as exc:
        await social_routes.follow(profile.id, request, (viewer, None), db_session)

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "COUNTRY_SIGNAL_CONFLICT"
    assert await db_session.scalar(select(Follow.id)) is None


@pytest.mark.asyncio
async def test_locked_posts_hide_body_and_reactions_are_unique(db_session):
    owner, profile = await creator(db_session, "post-owner@example.com")
    viewer, _ = await accounts.register(
        db_session, "post-viewer@example.com", "strong-password-123", None
    )
    post = await social.create_post(
        db_session,
        owner,
        {"post_type": "text", "body": "secret #Cosplay", "access_policy": AccessPolicy.followers},
    )
    await social.publish(db_session, owner, post.id)
    assert not await social.can_access_post(db_session, post, viewer)
    await social.follow(db_session, viewer, profile.id)
    assert await social.can_access_post(db_session, post, viewer)
    db_session.add(PostReaction(post_id=post.id, user_id=viewer.id))
    await db_session.flush()
    assert (
        await db_session.scalar(
            select(func.count()).select_from(PostReaction).where(PostReaction.post_id == post.id)
        )
        == 1
    )
    rows, cursor = await social.feed_posts(db_session, viewer, "following", None, None, 1)
    assert rows == [post] and cursor is None


@pytest.mark.asyncio
async def test_comment_reactions_are_access_checked_unique_and_visible_to_the_viewer(
    db_session, monkeypatch
):
    owner, _ = await creator(db_session, "comment-reaction-owner@example.com")
    viewer, _ = await accounts.register(
        db_session, "comment-reaction-viewer@example.com", "strong-password-123", None
    )
    post = await social.create_post(
        db_session,
        owner,
        {"post_type": "text", "body": "Comment reactions", "access_policy": AccessPolicy.free},
    )
    await social.publish(db_session, owner, post.id)
    comment = PostComment(post_id=post.id, user_id=owner.id, body="A comment to react to")
    db_session.add(comment)
    await db_session.flush()

    async def allowed(*_args, **_kwargs):
        return decision(allowed=True, feature=ComplianceFeature.platform_access)

    async def no_rate_limit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(social_routes, "request_feed_decision", allowed)
    monkeypatch.setattr(social_routes, "request_platform_decision", allowed)
    monkeypatch.setattr(social_routes, "enforce_social_rate_limit", no_rate_limit)
    request = Request({"type": "http", "client": ("127.0.0.1", 50000), "headers": []})

    await social_routes.react_to_comment(
        comment.id, ReactionInput(reaction_type="love"), request, (viewer, None), db_session
    )
    await social_routes.react_to_comment(
        comment.id, ReactionInput(reaction_type="fire"), request, (viewer, None), db_session
    )

    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(PostCommentReaction)
            .where(PostCommentReaction.comment_id == comment.id)
        )
        == 1
    )
    rows = await social_routes.comments(post.id, request, (viewer, None), db_session)
    assert rows[0]["reaction_count"] == 1
    assert rows[0]["reaction_counts"] == {"fire": 1}
    assert rows[0]["viewer_reaction"] == "fire"

    await social_routes.unreact_to_comment(comment.id, request, (viewer, None), db_session)
    rows = await social_routes.comments(post.id, request, (viewer, None), db_session)
    assert rows[0]["reaction_count"] == 0
    assert rows[0]["viewer_reaction"] is None


@pytest.mark.asyncio
async def test_scheduling_and_auto_posts_are_replay_safe(db_session):
    owner, profile = await creator(db_session, "schedule-owner@example.com")
    future = datetime.now(UTC) + timedelta(hours=1)
    scheduled = await social.create_post(
        db_session, owner, {"post_type": "text", "body": "later", "scheduled_at": future}
    )
    assert scheduled.status is FeedPostStatus.scheduled
    assert await social.publish_due_posts(db_session) == 0
    scheduled.scheduled_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await social.publish_due_posts(db_session) == 1
    assert scheduled.status is FeedPostStatus.published
    settings = await social.settings_for_creator(db_session, profile.id)
    settings.auto_post_galleries = True
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="New gallery",
        status=ContentStatus.published,
        access_policy=AccessPolicy.ppv,
    )
    db_session.add(content)
    await db_session.flush()
    assert await social.auto_post_content(db_session, content)
    assert await social.auto_post_content(db_session, content) is None
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(FeedPost)
            .where(FeedPost.source_content_id == content.id)
        )
        == 1
    )
    disabled = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="No announcement",
        status=ContentStatus.published,
        access_policy=AccessPolicy.free,
        feed_announcement_override=False,
    )
    db_session.add(disabled)
    await db_session.flush()
    assert await social.auto_post_content(db_session, disabled) is None
    enabled = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Forced announcement",
        status=ContentStatus.published,
        access_policy=AccessPolicy.free,
        feed_announcement_override=True,
    )
    db_session.add(enabled)
    await db_session.flush()
    settings.auto_post_galleries = False
    assert await social.auto_post_content(db_session, enabled)


@pytest.mark.asyncio
async def test_ppv_content_asset_cannot_be_reused_in_a_free_feed_post(db_session):
    owner, profile = await creator(db_session, "feed-media-isolation@example.com")
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Paid gallery",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=999,
        price_currency="EUR",
    )
    content.gallery = Gallery(preview_count=0)
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        moderation_status=ModerationStatus.approved,
        audience=MediaAudience.adult_restricted,
        storage_key="original/feed-isolation",
        original_filename="feed-isolation.jpg",
        mime_type="image/jpeg",
    )
    db_session.add_all([content, asset])
    await db_session.flush()
    db_session.add(
        GalleryItem(
            gallery_id=content.gallery.id,
            media_asset_id=asset.id,
            position=0,
        )
    )
    await db_session.flush()

    with pytest.raises(ValueError, match="dedicated to one content"):
        await social.create_post(
            db_session,
            owner,
            {
                "post_type": "image",
                "body": "Free reuse attempt",
                "access_policy": AccessPolicy.free,
                "media_asset_ids": [asset.id],
            },
        )

    assert (
        await db_session.scalar(
            select(FeedPostMedia.id).where(FeedPostMedia.media_asset_id == asset.id)
        )
        is None
    )


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


@pytest.mark.asyncio
async def test_restricted_content_reference_is_neutral_and_media_body_agree(
    db_session, reviewed_pt_compliance_policy
):
    owner, profile = await creator(db_session, "feed-restricted-reference@example.com")
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Explicit source title must not leak",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=1299,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    post = FeedPost(
        creator_id=profile.id,
        created_by_user_id=owner.id,
        post_type=FeedPostType.gallery_reference,
        body="Explicit announcement must not leak",
        status=FeedPostStatus.published,
        access_policy=AccessPolicy.free,
        published_at=datetime.now(UTC),
        source_content_id=content.id,
    )
    db_session.add(post)
    await db_session.flush()

    response = await post_response(
        db_session,
        post,
        None,
        decision(allowed=False, feature=ComplianceFeature.adult_media),
        decision(allowed=True, feature=ComplianceFeature.platform_access),
    )
    payload = response.model_dump()
    assert payload["body"] is None
    assert payload["media"] == []
    assert payload["locked"] is True
    assert payload["compliance_allowed"] is False
    assert payload["compliance_code"] == "AGE_VERIFICATION_REQUIRED"
    assert payload["compliance_reason"] == "Age verification is required"
    assert payload["content_reference"]["title"] == "Age-restricted content"
    assert payload["content_reference"]["price_amount_minor"] == 1299
    assert "Explicit" not in str(payload)


@pytest.mark.asyncio
async def test_text_only_feed_body_is_restricted_without_a_safe_classification(db_session):
    owner, profile = await creator(db_session, "text-only-restricted@example.com")
    post = FeedPost(
        creator_id=profile.id,
        created_by_user_id=owner.id,
        post_type=FeedPostType.text,
        body="Explicit text-only copy must not leak",
        status=FeedPostStatus.published,
        access_policy=AccessPolicy.free,
        published_at=datetime.now(UTC),
    )
    db_session.add(post)
    await db_session.flush()

    payload = (
        await post_response(
            db_session,
            post,
            None,
            decision(allowed=False, feature=ComplianceFeature.adult_media),
            decision(allowed=True, feature=ComplianceFeature.platform_access),
        )
    ).model_dump()

    assert payload["adult_access_required"] is True
    assert payload["body"] is None
    assert payload["locked"] is True
    assert "Explicit text-only" not in str(payload)


@pytest.mark.asyncio
async def test_video_content_reference_uses_authorized_trailer_or_playback(
    db_session, monkeypatch, reviewed_pt_compliance_policy
):
    owner, profile = await creator(db_session, "feed-video-reference@example.com")
    viewer, _ = await accounts.register(
        db_session, "feed-video-viewer@example.com", "strong-password-123", None
    )
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.video,
        status=MediaStatus.ready,
        moderation_status=ModerationStatus.approved,
        audience=MediaAudience.adult_restricted,
        storage_key="original/feed-video-reference.mp4",
        original_filename="feed-video-reference.mp4",
        mime_type="video/mp4",
        duration_seconds=90,
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.video,
        title="Private video reference",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=1299,
        price_currency="EUR",
    )
    db_session.add_all([asset, content])
    await db_session.flush()
    db_session.add_all(
        [
            VideoContent(
                content_id=content.id,
                source_media_asset_id=asset.id,
                preview_start_seconds=0,
                preview_duration_seconds=20,
            ),
            MediaDerivative(
                media_asset_id=asset.id,
                derivative_type=DerivativeType.poster,
                status=MediaStatus.ready,
                storage_key="derivative/feed-video-reference-poster.webp",
                mime_type="image/webp",
            ),
            MediaDerivative(
                media_asset_id=asset.id,
                derivative_type=DerivativeType.preview_clip,
                status=MediaStatus.ready,
                storage_key="derivative/feed-video-reference-preview.mp4",
                mime_type="video/mp4",
                duration_seconds=20,
            ),
            MediaDerivative(
                media_asset_id=asset.id,
                derivative_type=DerivativeType.playback,
                status=MediaStatus.ready,
                storage_key="derivative/feed-video-reference-playback.mp4",
                mime_type="video/mp4",
                duration_seconds=90,
            ),
        ]
    )
    post = FeedPost(
        creator_id=profile.id,
        created_by_user_id=owner.id,
        post_type=FeedPostType.video_reference,
        body="Watch this",
        status=FeedPostStatus.published,
        access_policy=AccessPolicy.free,
        published_at=datetime.now(UTC),
        source_content_id=content.id,
    )
    db_session.add(post)
    await db_session.flush()

    async def preview_allowed(*_args, **_kwargs):
        return True

    monkeypatch.setattr(social_routes, "can_access_preview", preview_allowed)

    allowed = decision(allowed=True, feature=ComplianceFeature.adult_media)
    locked_reference = (
        await post_response(
            db_session,
            post,
            viewer,
            allowed,
            decision(allowed=True, feature=ComplianceFeature.platform_access),
        )
    ).model_dump()["content_reference"]
    assert locked_reference["locked"]
    assert locked_reference["media_kind"] == "trailer"
    assert locked_reference["media_delivery_path"].startswith("/media/previews/")
    assert locked_reference["poster_delivery_path"].startswith("/media/previews/")

    content.access_policy = AccessPolicy.free
    playback_reference = (
        await post_response(
            db_session,
            post,
            viewer,
            allowed,
            decision(allowed=True, feature=ComplianceFeature.platform_access),
        )
    ).model_dump()["content_reference"]
    assert not playback_reference["locked"]
    assert playback_reference["media_kind"] == "playback"
    assert playback_reference["media_delivery_path"].startswith("/media/derivatives/")


@pytest.mark.asyncio
async def test_feed_response_exposes_durable_reaction_types(db_session):
    owner, profile = await creator(db_session, "feed-reactions-owner@example.com")
    viewer, _ = await accounts.register(
        db_session, "feed-reactions-viewer@example.com", "strong-password-123", None
    )
    other, _ = await accounts.register(
        db_session, "feed-reactions-other@example.com", "strong-password-123", None
    )
    post = FeedPost(
        creator_id=profile.id,
        created_by_user_id=owner.id,
        post_type=FeedPostType.text,
        body="Reaction summary",
        status=FeedPostStatus.published,
        access_policy=AccessPolicy.free,
        published_at=datetime.now(UTC),
    )
    db_session.add(post)
    await db_session.flush()
    db_session.add_all(
        [
            PostReaction(post_id=post.id, user_id=viewer.id, reaction_type=ReactionType.love),
            PostReaction(post_id=post.id, user_id=other.id, reaction_type=ReactionType.fire),
        ]
    )
    await db_session.flush()

    payload = (
        await post_response(
            db_session,
            post,
            viewer,
            decision(allowed=True, feature=ComplianceFeature.adult_media),
            decision(allowed=True, feature=ComplianceFeature.platform_access),
        )
    ).model_dump()

    assert payload["reaction_count"] == 2
    assert payload["reaction_counts"] == {"love": 1, "fire": 1}
    assert payload["viewer_reaction"] == "love"


@pytest.mark.asyncio
async def test_reaction_details_expose_only_public_creator_identities(db_session, monkeypatch):
    owner, profile = await creator(db_session, "reaction-details-owner@example.com")
    public_reactor, public_profile = await creator(
        db_session, "reaction-details-public@example.com"
    )
    private_reactor, _ = await accounts.register(
        db_session, "reaction-details-private@example.com", "strong-password-123", None
    )
    post = FeedPost(
        creator_id=profile.id,
        created_by_user_id=owner.id,
        post_type=FeedPostType.text,
        body="Reaction identities",
        status=FeedPostStatus.published,
        access_policy=AccessPolicy.free,
        published_at=datetime.now(UTC),
    )
    db_session.add(post)
    await db_session.flush()
    db_session.add_all(
        [
            PostReaction(
                post_id=post.id, user_id=public_reactor.id, reaction_type=ReactionType.love
            ),
            PostReaction(
                post_id=post.id, user_id=private_reactor.id, reaction_type=ReactionType.fire
            ),
        ]
    )
    await db_session.flush()

    async def allowed_feed(*_args, **_kwargs):
        return decision(allowed=True, feature=ComplianceFeature.adult_media)

    async def allowed_platform(*_args, **_kwargs):
        return decision(allowed=True, feature=ComplianceFeature.platform_access)

    monkeypatch.setattr(social_routes, "request_feed_decision", allowed_feed)
    monkeypatch.setattr(social_routes, "request_platform_decision", allowed_platform)
    request = Request({"type": "http", "method": "GET", "path": "/"})

    payload = await social_routes.reaction_details(
        post.id, request, (private_reactor, None), db_session
    )

    assert payload["total"] == 2
    assert payload["reaction_counts"] == {"love": 1, "fire": 1}
    assert {item["creator"]["username"] for item in payload["items"] if item["creator"]} == {
        public_profile.username
    }
    assert [item for item in payload["items"] if item["creator"] is None] == [
        {"reaction_type": "fire", "creator": None}
    ]
