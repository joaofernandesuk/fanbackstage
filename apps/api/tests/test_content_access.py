from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request

from app.accounts import service as accounts
from app.api.routes import media as media_routes
from app.api.routes.content import public_response
from app.compliance.types import ComplianceDecision
from app.content import access as content_access
from app.content import service as content_service
from app.content.access import can_access_asset, can_access_content, can_access_preview
from app.creators import service as creators
from app.models.compliance import AgeAssuranceLevel, ComplianceFeature
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
from app.models.creator import CreatorStatus
from app.models.social import FeedPost, FeedPostMedia, FeedPostStatus, FeedPostType


def canonical_media_decision(*, allowed: bool) -> ComplianceDecision:
    return ComplianceDecision(
        allowed=allowed,
        code="ALLOWED" if allowed else "AGE_VERIFICATION_REQUIRED",
        action=None if allowed else "VERIFY_AGE",
        reason="Policy allows access" if allowed else "Age verification is required",
        feature=ComplianceFeature.adult_media,
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


def media_request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
        }
    )


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
async def test_content_access_is_free_only_by_default_and_entitlement_is_explicit(db_session):
    owner, profile = await creator(db_session, "owner@example.com")
    viewer, _ = await accounts.register(
        db_session, "viewer@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Private gallery",
        status=ContentStatus.published,
        access_policy=AccessPolicy.ppv,
    )
    db_session.add(content)
    await db_session.flush()
    assert not await can_access_content(db_session, content, None)
    assert not await can_access_content(db_session, content, viewer)
    assert await can_access_content(db_session, content, owner)
    db_session.add(
        ContentEntitlement(
            subject_user_id=viewer.id,
            content_id=content.id,
            source_type="admin_grant",
            valid_from=datetime.now(UTC),
        )
    )
    await db_session.flush()
    assert await can_access_content(db_session, content, viewer)
    content.access_policy = AccessPolicy.free
    assert await can_access_content(db_session, content, None)


@pytest.mark.asyncio
async def test_non_free_policies_deny_without_an_entitlement(db_session):
    owner, profile = await creator(db_session, "policy-owner@example.com")
    viewer, _ = await accounts.register(
        db_session, "policy-viewer@example.com", "strong-password-123", None
    )
    for policy in (
        AccessPolicy.followers,
        AccessPolicy.subscription,
        AccessPolicy.ppv,
        AccessPolicy.private,
    ):
        content = ContentItem(
            owner_creator_id=profile.id,
            created_by_user_id=owner.id,
            content_type=ContentType.gallery,
            title=f"{policy.value} gallery",
            status=ContentStatus.published,
            access_policy=policy,
        )
        db_session.add(content)
        await db_session.flush()
        assert not await can_access_content(db_session, content, None)
        assert not await can_access_content(db_session, content, viewer)
        assert await can_access_content(db_session, content, owner)


@pytest.mark.asyncio
async def test_preview_requires_published_ready_configured_gallery_media(db_session):
    owner, profile = await creator(db_session, "preview-owner@example.com")
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        storage_key="private/source",
        original_filename="source.png",
        mime_type="image/png",
        audience=MediaAudience.safe_public,
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Gallery",
        status=ContentStatus.draft,
        moderation_status=ModerationStatus.approved,
    )
    content.gallery = Gallery(preview_count=1)
    db_session.add_all([asset, content])
    await db_session.flush()
    db_session.add(GalleryItem(gallery_id=content.gallery.id, media_asset_id=asset.id, position=0))
    derivative = MediaDerivative(
        media_asset_id=asset.id,
        derivative_type=DerivativeType.blurred_preview,
        status=MediaStatus.ready,
        storage_key="private/preview",
        mime_type="image/webp",
    )
    db_session.add(derivative)
    await db_session.flush()
    assert not await can_access_preview(db_session, derivative)
    content.status = ContentStatus.published
    assert await can_access_preview(db_session, derivative)
    with pytest.raises(ValueError, match="before review"):
        await content_service.configure_gallery_preview(
            db_session, owner, content.id, 1, {asset.id}
        )
    asset.moderation_status = ModerationStatus.rejected
    assert not await can_access_preview(db_session, derivative)


@pytest.mark.asyncio
async def test_public_content_response_only_includes_configured_derivative_paths(db_session):
    owner, profile = await creator(db_session, "response-owner@example.com")
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        storage_key="original/must-not-leak",
        original_filename="private.png",
        mime_type="image/png",
        audience=MediaAudience.safe_public,
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Published gallery",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
    )
    content.gallery = Gallery(preview_count=1)
    db_session.add_all([asset, content])
    await db_session.flush()
    db_session.add(GalleryItem(gallery_id=content.gallery.id, media_asset_id=asset.id, position=0))
    db_session.add(
        MediaDerivative(
            media_asset_id=asset.id,
            derivative_type=DerivativeType.blurred_preview,
            status=MediaStatus.ready,
            storage_key="derivative/private-preview",
            mime_type="image/webp",
        )
    )
    db_session.add(
        MediaDerivative(
            media_asset_id=asset.id,
            derivative_type=DerivativeType.display,
            status=MediaStatus.ready,
            storage_key="derivative/protected-display",
            mime_type="image/webp",
        )
    )
    await db_session.flush()
    payload = (await public_response(db_session, content, False)).model_dump(mode="json")
    assert payload["previews"]
    assert payload["previews"][0]["delivery_path"].startswith("/media/previews/")
    assert "original/must-not-leak" not in str(payload)
    assert "derivative/private-preview" not in str(payload)
    assert "derivative/protected-display" not in str(payload)
    assert payload["media"] == []


@pytest.mark.asyncio
async def test_reviewed_public_preview_switch_controls_projection_and_direct_delivery(
    db_session, monkeypatch
):
    owner, profile = await creator(db_session, "preview-policy-owner@example.com")
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        storage_key="derivative/policy-controlled-preview",
        original_filename="policy-preview.png",
        mime_type="image/png",
        audience=MediaAudience.safe_public,
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Policy-controlled preview",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
    )
    content.gallery = Gallery(preview_count=1)
    db_session.add_all([asset, content])
    await db_session.flush()
    db_session.add(
        GalleryItem(
            gallery_id=content.gallery.id,
            media_asset_id=asset.id,
            position=0,
            is_preview=True,
        )
    )
    derivative = MediaDerivative(
        media_asset_id=asset.id,
        derivative_type=DerivativeType.blurred_preview,
        status=MediaStatus.ready,
        storage_key="derivative/policy-controlled-preview",
        mime_type="image/webp",
    )
    db_session.add(derivative)
    await db_session.flush()
    decision = canonical_media_decision(allowed=True)

    async def disabled(*_args, **_kwargs):
        return SimpleNamespace(rules=SimpleNamespace(explicit_public_preview_allowed=False))

    async def enabled(*_args, **_kwargs):
        return SimpleNamespace(rules=SimpleNamespace(explicit_public_preview_allowed=True))

    async def resolved(*_args, **_kwargs):
        return decision

    async def no_rate_limit(*_args, **_kwargs):
        return None

    class Storage:
        def create_download_url(self, storage_key: str, ttl: int) -> str:
            assert storage_key == derivative.storage_key
            assert ttl > 0
            return "https://storage.example/policy-preview"

    monkeypatch.setattr(content_access, "effective_policy_for_country", disabled)
    projection = await public_response(db_session, content, False, adult_decision=decision)
    assert projection.previews == []
    monkeypatch.setattr(media_routes, "resolve_request_compliance_decision", resolved)
    monkeypatch.setattr(media_routes, "enforce_media_rate_limit", no_rate_limit)
    with pytest.raises(HTTPException) as denied:
        await media_routes.preview_delivery(
            derivative.id,
            media_request(f"/media/previews/{derivative.id}"),
            None,
            db_session,
        )
    assert denied.value.status_code == 404

    monkeypatch.setattr(content_access, "effective_policy_for_country", enabled)
    monkeypatch.setattr(media_routes, "storage_provider", lambda: Storage())
    allowed = await media_routes.preview_delivery(
        derivative.id,
        media_request(f"/media/previews/{derivative.id}"),
        None,
        db_session,
    )
    assert allowed.headers["location"] == "https://storage.example/policy-preview"


@pytest.mark.asyncio
async def test_restricted_full_media_requires_adult_decision_and_cross_creator_is_denied(
    db_session,
):
    owner, profile = await creator(db_session, "restricted-owner@example.com")
    _, other_profile = await creator(db_session, "restricted-other@example.com")
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Restricted gallery",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.free,
    )
    content.gallery = Gallery(preview_count=0)
    restricted = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        storage_key="original/restricted",
        original_filename="restricted.jpg",
        mime_type="image/jpeg",
        audience=MediaAudience.adult_restricted,
    )
    cross_creator = MediaAsset(
        owner_creator_id=other_profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        storage_key="original/cross-creator",
        original_filename="cross-creator.jpg",
        mime_type="image/jpeg",
        audience=MediaAudience.safe_public,
    )
    db_session.add_all([content, restricted, cross_creator])
    await db_session.flush()
    db_session.add_all(
        [
            GalleryItem(
                gallery_id=content.gallery.id,
                media_asset_id=restricted.id,
                position=0,
            ),
            GalleryItem(
                gallery_id=content.gallery.id,
                media_asset_id=cross_creator.id,
                position=1,
            ),
        ]
    )
    restricted_display = MediaDerivative(
        media_asset_id=restricted.id,
        derivative_type=DerivativeType.display,
        status=MediaStatus.ready,
        storage_key="derivative/restricted-display",
        mime_type="image/webp",
    )
    cross_display = MediaDerivative(
        media_asset_id=cross_creator.id,
        derivative_type=DerivativeType.display,
        status=MediaStatus.ready,
        storage_key="derivative/cross-display",
        mime_type="image/webp",
    )
    db_session.add_all([restricted_display, cross_display])
    await db_session.flush()
    assert not await can_access_asset(db_session, restricted.id, None)
    decision = canonical_media_decision(allowed=True)
    assert await can_access_asset(db_session, restricted.id, None, decision)
    assert not await can_access_asset(db_session, cross_creator.id, None, decision)
    projection = await public_response(
        db_session,
        content,
        True,
        adult_decision=decision,
    )
    assert [media.derivative_id for media in projection.media] == [restricted_display.id]


@pytest.mark.asyncio
async def test_subscription_entitlement_does_not_bypass_restricted_media_adult_gate(db_session):
    owner, profile = await creator(db_session, "subscriber-gate-owner@example.com")
    viewer, _ = await accounts.register(
        db_session,
        "subscriber-gate-viewer@example.com",
        "strong-password-123",
        None,
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Restricted subscriber gallery",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.subscription,
    )
    content.gallery = Gallery(preview_count=0)
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        storage_key="original/restricted-subscriber",
        original_filename="restricted-subscriber.jpg",
        mime_type="image/jpeg",
        audience=MediaAudience.adult_restricted,
    )
    db_session.add_all([content, asset])
    await db_session.flush()
    db_session.add_all(
        [
            GalleryItem(
                gallery_id=content.gallery.id,
                media_asset_id=asset.id,
                position=0,
            ),
            ContentEntitlement(
                subject_user_id=viewer.id,
                creator_id=profile.id,
                source_type="subscription",
                valid_from=datetime.now(UTC),
            ),
        ]
    )
    display = MediaDerivative(
        media_asset_id=asset.id,
        derivative_type=DerivativeType.display,
        status=MediaStatus.ready,
        storage_key="derivative/restricted-subscriber-display",
        mime_type="image/webp",
    )
    db_session.add(display)
    await db_session.flush()

    assert await can_access_content(db_session, content, viewer)
    assert not await can_access_asset(db_session, asset.id, viewer)
    gated = await public_response(db_session, content, False, viewer)
    assert gated.adult_access_required is True
    assert gated.adult_access_granted is False
    assert gated.media == []

    decision = canonical_media_decision(allowed=True)
    assert await can_access_asset(db_session, asset.id, viewer, decision)
    accessible = await public_response(db_session, content, True, viewer, decision)
    assert [media.derivative_id for media in accessible.media] == [display.id]


@pytest.mark.asyncio
async def test_direct_derivative_delivery_loads_authority_and_rechecks_compliance(
    db_session, monkeypatch, reviewed_pt_compliance_policy
):
    owner, profile = await creator(db_session, "direct-derivative-owner@example.com")
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Direct derivative authority",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.free,
    )
    content.gallery = Gallery(preview_count=0)
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        storage_key="original/direct-restricted",
        original_filename="direct-restricted.jpg",
        mime_type="image/jpeg",
        audience=MediaAudience.adult_restricted,
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
    derivative = MediaDerivative(
        media_asset_id=asset.id,
        derivative_type=DerivativeType.display,
        status=MediaStatus.ready,
        storage_key="derivative/direct-restricted",
        mime_type="image/webp",
    )
    db_session.add(derivative)
    await db_session.flush()

    async def no_rate_limit(*_args, **_kwargs):
        return None

    async def allowed(*_args, **_kwargs):
        return canonical_media_decision(allowed=True)

    class Storage:
        def create_download_url(self, storage_key: str, ttl: int) -> str:
            assert storage_key == derivative.storage_key
            assert ttl > 0
            return "https://storage.example/opaque-authorized-url"

    monkeypatch.setattr(media_routes, "enforce_media_rate_limit", no_rate_limit)
    monkeypatch.setattr(media_routes, "resolve_request_compliance_decision", allowed)
    monkeypatch.setattr(media_routes, "storage_provider", lambda: Storage())

    response = await media_routes.derivative_delivery(
        derivative.id,
        media_request(f"/media/derivatives/{derivative.id}"),
        None,
        db_session,
    )
    assert response.headers["location"] == "https://storage.example/opaque-authorized-url"
    assert response.headers["cache-control"] == "private, no-store"


@pytest.mark.asyncio
async def test_ambiguous_content_and_feed_media_context_denies_direct_derivative(
    db_session, monkeypatch
):
    owner, profile = await creator(db_session, "ambiguous-media-owner@example.com")
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Paid source",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
    )
    content.gallery = Gallery(preview_count=0)
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        storage_key="original/ambiguous-context",
        original_filename="ambiguous.jpg",
        mime_type="image/jpeg",
        audience=MediaAudience.adult_restricted,
    )
    post = FeedPost(
        creator_id=profile.id,
        created_by_user_id=owner.id,
        post_type=FeedPostType.image,
        body="Corrupt free reuse",
        status=FeedPostStatus.published,
        access_policy=AccessPolicy.free,
        published_at=datetime.now(UTC),
    )
    db_session.add_all([content, asset, post])
    await db_session.flush()
    db_session.add_all(
        [
            GalleryItem(
                gallery_id=content.gallery.id,
                media_asset_id=asset.id,
                position=0,
            ),
            FeedPostMedia(post_id=post.id, media_asset_id=asset.id, position=0),
        ]
    )
    derivative = MediaDerivative(
        media_asset_id=asset.id,
        derivative_type=DerivativeType.display,
        status=MediaStatus.ready,
        storage_key="derivative/ambiguous-context",
        mime_type="image/webp",
    )
    db_session.add(derivative)
    await db_session.flush()

    async def no_rate_limit(*_args, **_kwargs):
        return None

    async def allowed(*_args, **_kwargs):
        return canonical_media_decision(allowed=True)

    class Storage:
        def create_download_url(self, *_args, **_kwargs):
            raise AssertionError("Ambiguous media must not mint a storage URL")

    assert not await can_access_asset(
        db_session, asset.id, owner, canonical_media_decision(allowed=True)
    )
    monkeypatch.setattr(media_routes, "enforce_media_rate_limit", no_rate_limit)
    monkeypatch.setattr(media_routes, "resolve_request_compliance_decision", allowed)
    monkeypatch.setattr(media_routes, "storage_provider", lambda: Storage())
    with pytest.raises(HTTPException) as denied:
        await media_routes.derivative_delivery(
            derivative.id,
            media_request(f"/media/derivatives/{derivative.id}"),
            (owner, None),
            db_session,
        )
    assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_final_media_delivery_does_not_treat_staff_role_as_entitlement_or_owner_as_age(
    db_session, monkeypatch
):
    owner, profile = await creator(db_session, "final-authority-owner@example.com")
    administrator, _ = await accounts.register(
        db_session,
        "final-authority-admin@example.com",
        "strong-password-123",
        None,
    )
    await accounts.assign_role(
        db_session, administrator, "admin", administrator.id, "test authority"
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Paid restricted authority",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
    )
    content.gallery = Gallery(preview_count=0)
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        audience=MediaAudience.adult_restricted,
        storage_key="original/final-authority",
        original_filename="final-authority.jpg",
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
    derivative = MediaDerivative(
        media_asset_id=asset.id,
        derivative_type=DerivativeType.display,
        status=MediaStatus.ready,
        storage_key="derivative/final-authority",
        mime_type="image/webp",
    )
    db_session.add(derivative)
    await db_session.flush()

    async def no_rate_limit(*_args, **_kwargs):
        return None

    async def denied(*_args, **_kwargs):
        return canonical_media_decision(allowed=False)

    async def allowed(*_args, **_kwargs):
        return canonical_media_decision(allowed=True)

    class Storage:
        def create_download_url(self, *_args, **_kwargs):
            raise AssertionError("A denied viewer must not mint a storage URL")

    monkeypatch.setattr(media_routes, "enforce_media_rate_limit", no_rate_limit)
    monkeypatch.setattr(media_routes, "storage_provider", lambda: Storage())
    monkeypatch.setattr(media_routes, "resolve_request_compliance_decision", denied)
    with pytest.raises(HTTPException) as owner_denied:
        await media_routes.derivative_delivery(
            derivative.id,
            media_request(f"/media/derivatives/{derivative.id}"),
            (owner, None),
            db_session,
        )
    assert owner_denied.value.status_code == 404

    monkeypatch.setattr(media_routes, "resolve_request_compliance_decision", allowed)
    with pytest.raises(HTTPException) as staff_denied:
        await media_routes.derivative_delivery(
            derivative.id,
            media_request(f"/media/derivatives/{derivative.id}"),
            (administrator, None),
            db_session,
        )
    assert staff_denied.value.status_code == 404

    async def denied(*_args, **_kwargs):
        return canonical_media_decision(allowed=False)

    monkeypatch.setattr(media_routes, "resolve_request_compliance_decision", denied)
    with pytest.raises(HTTPException) as blocked:
        await media_routes.derivative_delivery(
            derivative.id,
            media_request(f"/media/derivatives/{derivative.id}"),
            None,
            db_session,
        )
    assert blocked.value.status_code == 404

    with pytest.raises(HTTPException) as missing:
        await media_routes.derivative_delivery(
            uuid4(),
            media_request("/media/derivatives/missing"),
            None,
            db_session,
        )
    assert missing.value.status_code == 404
