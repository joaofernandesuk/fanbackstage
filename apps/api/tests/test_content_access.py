from datetime import UTC, datetime

import pytest

from app.accounts import service as accounts
from app.api.routes.content import public_response
from app.content.access import can_access_content, can_access_preview
from app.creators import service as creators
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
    MediaDerivative,
    MediaStatus,
    MediaType,
    ModerationStatus,
)
from app.models.creator import CreatorStatus


async def creator(db, email: str):
    user, _ = await accounts.register(db, email, "strong-password-123", None)
    profile = await creators.get_or_create_profile(db, user)
    await creators.update_profile(
        db, profile, {"username": email.split("@")[0], "display_name": "Creator"}, user.id
    )
    await creators.submit(db, profile, user.id)
    await creators.development_verify(db, profile, True, user.id)
    await creators.set_status(db, profile, CreatorStatus.approved, user.id)
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
async def test_preview_requires_published_ready_configured_gallery_media(db_session):
    owner, profile = await creator(db_session, "preview-owner@example.com")
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        storage_key="private/source",
        original_filename="source.png",
        mime_type="image/png",
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Gallery",
        status=ContentStatus.draft,
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
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Published gallery",
        status=ContentStatus.published,
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
    await db_session.flush()
    payload = (await public_response(db_session, content, True)).model_dump(mode="json")
    assert payload["previews"]
    assert payload["previews"][0]["delivery_path"].startswith("/media/previews/")
    assert "original/must-not-leak" not in str(payload)
    assert "derivative/private-preview" not in str(payload)
