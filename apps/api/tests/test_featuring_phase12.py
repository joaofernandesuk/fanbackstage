from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.accounts import service as accounts
from app.creators import service as creators
from app.featuring import service
from app.models.creator import CreatorStatus
from app.models.featuring import FeatureBookingStatus
from app.models.finance import LedgerEntry, PaymentStatus


async def creator(db, email: str):
    user, _ = await accounts.register(db, email, "strong-password-123", None)
    profile = await creators.get_or_create_profile(db, user)
    await creators.update_profile(
        db,
        profile,
        {"username": email.split("@")[0], "display_name": email.split("@")[0]},
        user.id,
    )
    await creators.submit(db, profile, user.id)
    await creators.development_verify(db, profile, True, user.id)
    await creators.set_status(db, profile, CreatorStatus.approved, user.id)
    profile.is_public = True
    return user, profile


@pytest.mark.asyncio
async def test_feature_booking_snapshots_price_reserves_slot_and_settles_platform_revenue(
    db_session,
):
    admin, _ = await accounts.register(
        db_session, "feature-admin@example.com", "strong-password-123", None
    )
    owner, profile = await creator(db_session, "feature-owner@example.com")
    surface = await service.create_surface(db_session, admin, "discover_creators")
    slot = await service.create_slot(db_session, admin, surface.id, "hero-1", 0)
    first_price = await service.create_price(
        db_session, admin, slot.id, "creator", 3600, 900, "eur"
    )
    start = datetime.now(UTC) + timedelta(hours=2)
    booking = await service.create_booking(
        db_session,
        actor=owner,
        purchaser=owner,
        slot_id=slot.id,
        target_type="creator",
        target_id=profile.id,
        starts_at=start,
        duration_seconds=3600,
        idempotency_key="feature-owner-1",
    )
    await service.create_price(db_session, admin, slot.id, "creator", 3600, 1200, "EUR")
    assert booking.price_minor == 900 and booking.price_version == first_price.version
    with pytest.raises(service.FeaturingError, match="no longer available"):
        await service.create_booking(
            db_session,
            actor=owner,
            purchaser=owner,
            slot_id=slot.id,
            target_type="creator",
            target_id=profile.id,
            starts_at=start,
            duration_seconds=3600,
            idempotency_key="feature-owner-2",
        )
    attempt = await service.initiate_payment(db_session, booking, owner)
    attempt.status = PaymentStatus.succeeded
    await service.settle_payment(db_session, booking)
    assert booking.status is FeatureBookingStatus.scheduled
    assert len((await db_session.scalars(select(LedgerEntry))).all()) == 2


@pytest.mark.asyncio
async def test_feature_booking_rejects_another_creators_target_and_payer_spoofing(db_session):
    admin, _ = await accounts.register(
        db_session, "feature-admin-2@example.com", "strong-password-123", None
    )
    owner, profile = await creator(db_session, "feature-owner-2@example.com")
    intruder, _ = await creator(db_session, "feature-intruder@example.com")
    surface = await service.create_surface(db_session, admin, "discover_content")
    slot = await service.create_slot(db_session, admin, surface.id, "content-1", 0)
    await service.create_price(db_session, admin, slot.id, "creator", 3600, 900, "EUR")
    with pytest.raises(service.FeaturingError, match="not authorized"):
        await service.create_booking(
            db_session,
            actor=intruder,
            purchaser=intruder,
            slot_id=slot.id,
            target_type="creator",
            target_id=profile.id,
            starts_at=datetime.now(UTC) + timedelta(hours=2),
            duration_seconds=3600,
            idempotency_key="intruder-feature",
        )
    booking = await service.create_booking(
        db_session,
        actor=owner,
        purchaser=owner,
        slot_id=slot.id,
        target_type="creator",
        target_id=profile.id,
        starts_at=datetime.now(UTC) + timedelta(hours=2),
        duration_seconds=3600,
        idempotency_key="owner-feature",
    )
    with pytest.raises(service.FeaturingError, match="selected payer"):
        await service.initiate_payment(db_session, booking, intruder)
