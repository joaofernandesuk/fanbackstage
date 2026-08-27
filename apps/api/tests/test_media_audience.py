from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.accounts import service as accounts
from app.accounts.adult_access import (
    AdultAccessDecision,
    AdultAccessSource,
    AdultAssurance,
)
from app.api.routes.media import _delivery_ttl
from app.creators import service as creators
from app.media import service as media
from app.models.audit import AuditEvent
from app.models.content import MediaAsset, MediaAudience, MediaStatus, MediaType
from app.models.creator import CreatorStatus


async def creator(db):
    user, _ = await accounts.register(db, "audience-owner@example.com", "strong-password-123", None)
    profile = await creators.get_or_create_profile(db, user)
    await creators.update_profile(
        db,
        profile,
        {"username": "audience-owner", "display_name": "Audience Owner"},
        user.id,
    )
    await creators.submit(db, profile, user.id)
    await creators.development_verify(db, profile, True, user.id)
    await creators.set_status(db, profile, CreatorStatus.approved, user.id)
    return profile


@pytest.mark.asyncio
async def test_media_audience_is_fail_closed_moderator_owned_and_replay_safe(db_session):
    profile = await creator(db_session)
    viewer, _ = await accounts.register(
        db_session, "audience-viewer@example.com", "strong-password-123", None
    )
    moderator, _ = await accounts.register(
        db_session, "audience-moderator@example.com", "strong-password-123", None
    )
    await accounts.assign_role(db_session, moderator, "moderator", moderator.id, None)
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        storage_key="original/audience-classification",
        original_filename="audience.jpg",
        mime_type="image/jpeg",
    )
    db_session.add(asset)
    await db_session.flush()
    assert asset.audience is MediaAudience.adult_restricted

    with pytest.raises(HTTPException) as denied:
        await media.classify_audience(db_session, viewer, asset.id, MediaAudience.safe_public)
    assert denied.value.status_code == 403

    classified, changed = await media.classify_audience(
        db_session, moderator, asset.id, MediaAudience.safe_public
    )
    assert classified.audience is MediaAudience.safe_public
    assert changed is True
    _, repeated = await media.classify_audience(
        db_session, moderator, asset.id, MediaAudience.safe_public
    )
    assert repeated is False
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == "media.audience_classified")
        )
        == 1
    )


def test_restricted_route_delivery_ttl_caps_guest_and_safe_public_is_unaffected(monkeypatch):
    monkeypatch.setattr(
        "app.api.routes.media.get_settings",
        lambda: type("Settings", (), {"media_url_ttl_seconds": 300})(),
    )
    now = datetime.now(UTC)
    guest = AdultAccessDecision(
        allowed=True,
        assurance=AdultAssurance.self_attested,
        source=AdultAccessSource.cookie,
        policy_version="v1",
        expires_at=now + timedelta(seconds=2, microseconds=500_000),
    )
    restricted = MediaAsset(audience=MediaAudience.adult_restricted)
    assert 1 <= _delivery_ttl(restricted, guest) <= 2
    near_expiry = AdultAccessDecision(
        allowed=True,
        assurance=AdultAssurance.self_attested,
        source=AdultAccessSource.cookie,
        policy_version="v1",
        expires_at=now + timedelta(milliseconds=250),
    )
    with pytest.raises(ValueError, match="unavailable"):
        _delivery_ttl(restricted, near_expiry)

    safe = MediaAsset(audience=MediaAudience.safe_public)
    assert _delivery_ttl(safe, guest) == 300
