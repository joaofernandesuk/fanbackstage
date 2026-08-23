import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.accounts import service as accounts
from app.core.config import get_settings
from app.creators import service as creators
from app.db.session import SessionLocal
from app.discovery import service as discovery
from app.featuring import service
from app.finance import service as finance
from app.models.creator import CreatorStatus
from app.models.featuring import FeatureBookingStatus, FeatureIneligibilityReason, FeatureRefund
from app.models.finance import LedgerEntry, PaymentStatus
from app.models.identity import User
from app.models.messaging import UserBlock


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


@pytest.mark.asyncio
async def test_platform_ineligibility_refunds_exact_unused_time_once(db_session):
    admin, _ = await accounts.register(
        db_session, "feature-admin-3@example.com", "strong-password-123", None
    )
    owner, profile = await creator(db_session, "feature-owner-3@example.com")
    surface = await service.create_surface(db_session, admin, "live_now")
    slot = await service.create_slot(db_session, admin, surface.id, "live-1", 0)
    await service.create_price(db_session, admin, slot.id, "creator", 3600, 901, "EUR")
    booking = await service.create_booking(
        db_session,
        actor=owner,
        purchaser=owner,
        slot_id=slot.id,
        target_type="creator",
        target_id=profile.id,
        starts_at=datetime.now(UTC) + timedelta(hours=2),
        duration_seconds=3600,
        idempotency_key="refund-booking",
    )
    attempt = await service.initiate_payment(db_session, booking, owner)
    attempt.status = PaymentStatus.succeeded
    await service.settle_payment(db_session, booking)
    # No activation means the platform/moderation failure returns the entire immutable snapshot.
    await service.terminate_ineligible(
        db_session, booking, FeatureIneligibilityReason.moderation_ineligible
    )
    assert booking.status is FeatureBookingStatus.refunded
    assert (await db_session.scalar(select(FeatureRefund.amount_minor))) == 901
    await service.terminate_ineligible(
        db_session, booking, FeatureIneligibilityReason.moderation_ineligible
    )
    assert len((await db_session.scalars(select(FeatureRefund))).all()) == 1

    # A separately paid, partially delivered placement floors deterministically: 901 * 1800 / 3600 = 450.
    partial = await service.create_booking(
        db_session,
        actor=owner,
        purchaser=owner,
        slot_id=slot.id,
        target_type="creator",
        target_id=profile.id,
        starts_at=datetime.now(UTC) + timedelta(hours=4),
        duration_seconds=3600,
        idempotency_key="partial-refund-booking",
    )
    partial_attempt = await service.initiate_payment(db_session, partial, owner)
    partial_attempt.status = PaymentStatus.succeeded
    await service.settle_payment(db_session, partial)
    partial.status = FeatureBookingStatus.active
    partial.activated_at = datetime.now(UTC) - timedelta(seconds=1800)
    partial.ends_at = datetime.now(UTC) + timedelta(seconds=1800)
    await service.terminate_ineligible(
        db_session, partial, FeatureIneligibilityReason.platform_failure
    )
    refunds = (
        await db_session.scalars(select(FeatureRefund).order_by(FeatureRefund.amount_minor))
    ).all()
    assert [row.amount_minor for row in refunds] == [450, 901]


@pytest.mark.asyncio
async def test_creator_end_and_cancellation_cutoff_do_not_mutate_policy_snapshot(db_session):
    admin, _ = await accounts.register(
        db_session, "feature-admin-4@example.com", "strong-password-123", None
    )
    owner, profile = await creator(db_session, "feature-owner-4@example.com")
    surface = await service.create_surface(
        db_session, admin, "discover_home_hero", cancellation_cutoff_seconds=3600
    )
    slot = await service.create_slot(db_session, admin, surface.id, "hero-1", 0)
    await service.create_price(db_session, admin, slot.id, "creator", 3600, 900, "EUR")
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
        idempotency_key="cutoff-booking",
    )
    attempt = await service.initiate_payment(db_session, booking, owner)
    attempt.status = PaymentStatus.succeeded
    await service.settle_payment(db_session, booking)
    await service.cancel_before_start(db_session, booking, owner, now=start - timedelta(minutes=60))
    assert booking.status is FeatureBookingStatus.refunded

    late = await service.create_booking(
        db_session,
        actor=owner,
        purchaser=owner,
        slot_id=slot.id,
        target_type="creator",
        target_id=profile.id,
        starts_at=start + timedelta(hours=2),
        duration_seconds=3600,
        idempotency_key="late-cutoff-booking",
    )
    late_attempt = await service.initiate_payment(db_session, late, owner)
    late_attempt.status = PaymentStatus.succeeded
    await service.settle_payment(db_session, late)
    await service.cancel_before_start(
        db_session, late, owner, now=late.starts_at - timedelta(minutes=59)
    )
    assert late.status is FeatureBookingStatus.cancelled

    voluntary = await service.create_booking(
        db_session,
        actor=owner,
        purchaser=owner,
        slot_id=slot.id,
        target_type="creator",
        target_id=profile.id,
        starts_at=start + timedelta(hours=4),
        duration_seconds=3600,
        idempotency_key="creator-ended-booking",
    )
    voluntary_attempt = await service.initiate_payment(db_session, voluntary, owner)
    voluntary_attempt.status = PaymentStatus.succeeded
    await service.settle_payment(db_session, voluntary)
    voluntary.status = FeatureBookingStatus.active
    voluntary.activated_at = datetime.now(UTC) - timedelta(seconds=120)
    await service.terminate_ineligible(
        db_session, voluntary, FeatureIneligibilityReason.creator_ended
    )
    assert voluntary.status is FeatureBookingStatus.suspended
    assert (
        await db_session.scalar(
            select(FeatureRefund).where(FeatureRefund.booking_id == voluntary.id)
        )
    ) is None


@pytest.mark.asyncio
async def test_sponsored_insertion_is_labelled_deduplicated_and_never_changes_organic_eligibility(
    db_session,
):
    admin, _ = await accounts.register(
        db_session, "feature-admin-5@example.com", "strong-password-123", None
    )
    owner, profile = await creator(db_session, "relevant-feature@example.com")
    surface = await service.create_surface(db_session, admin, "discover_home_hero")
    slot = await service.create_slot(db_session, admin, surface.id, "hero-1", 0)
    await service.create_price(db_session, admin, slot.id, "creator", 3600, 900, "EUR")
    booking = await service.create_booking(
        db_session,
        actor=owner,
        purchaser=owner,
        slot_id=slot.id,
        target_type="creator",
        target_id=profile.id,
        starts_at=datetime.now(UTC) + timedelta(hours=2),
        duration_seconds=3600,
        idempotency_key="sponsored-query",
    )
    booking.status = FeatureBookingStatus.active
    organic, _, _ = await discovery.search(
        db_session, None, query="relevant", entity_types={"creator"}
    )
    featured, _, _ = await discovery.search(
        db_session,
        None,
        query="relevant",
        entity_types={"creator"},
        feature_surface="discover_home_hero",
    )
    assert [item.id for item in organic] == [profile.id]
    assert [item.id for item in featured] == [profile.id]
    assert featured[0].sponsored and featured[0].placement_type == "sponsored"
    assert featured[0].sponsored_surface == "discover_home_hero"
    # The paid booking cannot bypass the same organic candidate eligibility.
    irrelevant, _, _ = await discovery.search(
        db_session,
        None,
        query="unrelated",
        entity_types={"creator"},
        feature_surface="discover_home_hero",
    )
    assert irrelevant == []
    viewer, _ = await accounts.register(
        db_session, "feature-blocked-viewer@example.com", "strong-password-123", None
    )
    db_session.add(UserBlock(blocker_user_id=viewer.id, blocked_user_id=owner.id))
    blocked, _, _ = await discovery.search(
        db_session,
        viewer,
        query="relevant",
        entity_types={"creator"},
        feature_surface="discover_home_hero",
    )
    assert blocked == []


@pytest.mark.asyncio
async def test_payment_webhook_replay_and_chargeback_reverse_platform_revenue_once(db_session):
    admin, _ = await accounts.register(
        db_session, "feature-admin-6@example.com", "strong-password-123", None
    )
    owner, profile = await creator(db_session, "payment-replay-feature@example.com")
    surface = await service.create_surface(db_session, admin, "discover_creators")
    slot = await service.create_slot(db_session, admin, surface.id, "replay-1", 0)
    await service.create_price(db_session, admin, slot.id, "creator", 3600, 900, "EUR")
    booking = await service.create_booking(
        db_session,
        actor=owner,
        purchaser=owner,
        slot_id=slot.id,
        target_type="creator",
        target_id=profile.id,
        starts_at=datetime.now(UTC) + timedelta(hours=2),
        duration_seconds=3600,
        idempotency_key="payment-replay",
    )
    attempt = await service.initiate_payment(db_session, booking, owner)
    payload, signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db_session, payload, signature)
    await finance.process_development_webhook(db_session, payload, signature)
    assert booking.status is FeatureBookingStatus.scheduled
    assert len((await db_session.scalars(select(LedgerEntry))).all()) == 2

    chargeback_payload = json.dumps(
        {
            "id": f"chargeback-{attempt.id}",
            "type": "payment.chargeback",
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    chargeback_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(), chargeback_payload, hashlib.sha256
    ).hexdigest()
    await finance.process_development_webhook(db_session, chargeback_payload, chargeback_signature)
    await finance.process_development_webhook(db_session, chargeback_payload, chargeback_signature)
    assert booking.status is FeatureBookingStatus.chargeback
    assert len((await db_session.scalars(select(LedgerEntry))).all()) == 4


@pytest.mark.asyncio
async def test_final_slot_concurrent_reservation_has_exactly_one_winner(db_session):
    admin, _ = await accounts.register(
        db_session, "feature-admin-7@example.com", "strong-password-123", None
    )
    first_user, first_profile = await creator(db_session, "race-first@example.com")
    second_user, second_profile = await creator(db_session, "race-second@example.com")
    surface = await service.create_surface(db_session, admin, "discover_content")
    slot = await service.create_slot(db_session, admin, surface.id, "race-1", 0, capacity=1)
    await service.create_price(db_session, admin, slot.id, "creator", 3600, 900, "EUR")
    await db_session.commit()
    starts_at = datetime.now(UTC) + timedelta(hours=3)

    async def reserve(user_id, profile_id, key):
        async with SessionLocal() as session:
            actor = await session.get(User, user_id)
            assert actor
            try:
                await service.create_booking(
                    session,
                    actor=actor,
                    purchaser=actor,
                    slot_id=slot.id,
                    target_type="creator",
                    target_id=profile_id,
                    starts_at=starts_at,
                    duration_seconds=3600,
                    idempotency_key=key,
                )
                await session.commit()
                return "won"
            except service.FeaturingError:
                await session.rollback()
                return "lost"

    outcomes = await asyncio.gather(
        reserve(first_user.id, first_profile.id, "race-one"),
        reserve(second_user.id, second_profile.id, "race-two"),
    )
    assert sorted(outcomes) == ["lost", "won"]
