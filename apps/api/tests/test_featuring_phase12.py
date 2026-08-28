import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import trusted_self_attested_accounts as accounts
from fastapi import HTTPException
from sqlalchemy import func, select
from starlette.requests import Request

from app.api.routes import featuring as featuring_routes
from app.core.config import Settings, get_settings
from app.creators import service as creators
from app.db.session import SessionLocal
from app.discovery import service as discovery
from app.featuring import service
from app.finance import service as finance
from app.models.content import ModerationStatus
from app.models.creator import CreatorStatus
from app.models.discovery import DiscoveryEvent
from app.models.featuring import (
    FeatureBooking,
    FeatureBookingPaymentAttempt,
    FeatureBookingStatus,
    FeatureIneligibilityReason,
    FeatureRefund,
)
from app.models.finance import (
    ExcessCaptureSource,
    LedgerDirection,
    LedgerEntry,
    LedgerTransaction,
    PaymentAttempt,
    PaymentRefundRequirement,
    PaymentStatus,
)
from app.models.identity import User
from app.models.marketplace import (
    MarketplaceCondition,
    MarketplaceListing,
    MarketplaceListingStatus,
    MarketplaceShippingMode,
)
from app.models.messaging import UserBlock
from app.models.streaming import LiveAccessMode, LiveRoom, LiveRoomStatus
from app.schemas.featuring import BookingInput


async def creator(db, email: str):
    user, _ = await accounts.register(
        db,
        email,
        "strong-password-123",
        None,
        country_code="PT",
    )
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


def signed_payment_event(
    attempt: PaymentAttempt, event_type: str, event_id: str
) -> tuple[bytes, str]:
    payload = json.dumps(
        {
            "id": event_id,
            "type": event_type,
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(
        get_settings().payment_webhook_secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return payload, signature


@pytest.mark.asyncio
async def test_unattested_featuring_api_fails_safely_without_booking_or_attempt(db_session):
    admin, _ = await accounts.register(
        db_session, "feature-unattested-admin@example.com", "strong-password-123", None
    )
    owner, profile = await creator(db_session, "feature-unattested-owner@example.com")
    surface = await service.create_surface(db_session, admin, "discover_creators")
    slot = await service.create_slot(db_session, admin, surface.id, "unattested-slot", 0)
    await service.create_price(db_session, admin, slot.id, "creator", 3600, 900, "EUR")
    owner.adult_attested_at = None
    owner.adult_attestation_version = None

    with pytest.raises(HTTPException) as exc:
        await featuring_routes.create_booking(
            BookingInput(
                slot_id=slot.id,
                target_type="creator",
                target_id=profile.id,
                starts_at=datetime.now(UTC) + timedelta(hours=2),
                duration_seconds=3600,
            ),
            Request({"type": "http", "client": ("127.0.0.1", 50000), "headers": []}),
            (owner, None),
            db_session,
            "feature-unattested",
        )
    assert exc.value.status_code == 403
    assert "Age verification is required" in str(exc.value.detail)
    assert await db_session.scalar(select(FeatureBooking.id)) is None
    assert await db_session.scalar(select(PaymentAttempt.id)) is None


@pytest.mark.asyncio
async def test_featuring_trusted_country_conflict_creates_no_booking_or_attempt(
    db_session, monkeypatch
):
    admin, _ = await accounts.register(
        db_session,
        "feature-country-conflict-admin@example.com",
        "strong-password-123",
        None,
    )
    owner, profile = await creator(db_session, "feature-country-conflict-owner@example.com")
    owner.country_code = "PT"
    surface = await service.create_surface(db_session, admin, "discover_creators")
    slot = await service.create_slot(db_session, admin, surface.id, "country-conflict-slot", 0)
    await service.create_price(db_session, admin, slot.id, "creator", 3600, 900, "EUR")
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
        await featuring_routes.create_booking(
            BookingInput(
                slot_id=slot.id,
                target_type="creator",
                target_id=profile.id,
                starts_at=datetime.now(UTC) + timedelta(hours=2),
                duration_seconds=3600,
            ),
            request,
            (owner, None),
            db_session,
            "feature-country-conflict",
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "COUNTRY_SIGNAL_CONFLICT"
    assert await db_session.scalar(select(FeatureBooking.id)) is None
    assert await db_session.scalar(select(PaymentAttempt.id)) is None


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
async def test_failed_featuring_payment_retries_without_repricing_or_duplicate_settlement(
    db_session,
):
    admin, _ = await accounts.register(
        db_session, "feature-retry-admin@example.com", "strong-password-123", None
    )
    owner, profile = await creator(db_session, "feature-retry-owner@example.com")
    surface = await service.create_surface(db_session, admin, "discover_creators")
    slot = await service.create_slot(db_session, admin, surface.id, "retry-slot", 0)
    await service.create_price(db_session, admin, slot.id, "creator", 3600, 900, "EUR")
    starts_at = datetime.now(UTC) + timedelta(hours=2)
    booking = await service.create_booking(
        db_session,
        actor=owner,
        purchaser=owner,
        slot_id=slot.id,
        target_type="creator",
        target_id=profile.id,
        starts_at=starts_at,
        duration_seconds=3600,
        idempotency_key="feature-retry-booking",
    )
    snapshot = (
        booking.slot_id,
        booking.starts_at,
        booking.ends_at,
        booking.duration_seconds,
        booking.price_minor,
        booking.currency,
        booking.price_version,
        booking.reservation_expires_at,
    )
    first = await service.initiate_payment(db_session, booking, owner, "feature-retry-payment-1")
    failed_payload, failed_signature = signed_payment_event(
        first, "payment.failed", f"feature-payment-failed-{first.id}"
    )
    await finance.process_development_webhook(db_session, failed_payload, failed_signature)
    await finance.process_development_webhook(db_session, failed_payload, failed_signature)
    assert booking.status is FeatureBookingStatus.failed
    assert booking.reservation_expires_at == snapshot[-1]

    # A provider decline does not release the still-live reservation.
    with pytest.raises(service.FeaturingError, match="no longer available"):
        await service.create_booking(
            db_session,
            actor=owner,
            purchaser=owner,
            slot_id=slot.id,
            target_type="creator",
            target_id=profile.id,
            starts_at=starts_at,
            duration_seconds=3600,
            idempotency_key="feature-retry-competing-booking",
        )

    replay = await service.initiate_payment(db_session, booking, owner, "feature-retry-payment-1")
    assert replay.id == first.id and replay.status is PaymentStatus.failed
    retry = await service.initiate_payment(db_session, booking, owner, "feature-retry-payment-2")
    assert retry.id != first.id
    assert booking.status is FeatureBookingStatus.awaiting_payment
    assert (
        booking.slot_id,
        booking.starts_at,
        booking.ends_at,
        booking.duration_seconds,
        booking.price_minor,
        booking.currency,
        booking.price_version,
        booking.reservation_expires_at,
    ) == snapshot
    history = (
        await db_session.scalars(
            select(FeatureBookingPaymentAttempt)
            .where(FeatureBookingPaymentAttempt.booking_id == booking.id)
            .order_by(FeatureBookingPaymentAttempt.attempt_number)
        )
    ).all()
    assert [(row.payment_attempt_id, row.attempt_number) for row in history] == [
        (first.id, 1),
        (retry.id, 2),
    ]

    # The first verified success wins even when it belongs to the older attempt.
    first_success, first_signature = signed_payment_event(
        first, "payment.succeeded", f"feature-payment-late-success-{first.id}"
    )
    await finance.process_development_webhook(db_session, first_success, first_signature)
    await finance.process_development_webhook(db_session, first_success, first_signature)
    assert booking.status is FeatureBookingStatus.scheduled
    assert booking.payment_attempt_id == first.id
    assert len((await db_session.scalars(select(LedgerEntry))).all()) == 2

    # A later capture is frozen as refund-required financial truth, never settled twice.
    retry_success, retry_signature = finance.development_webhook_payload(retry)
    await finance.process_development_webhook(db_session, retry_success, retry_signature)
    await finance.process_development_webhook(db_session, retry_success, retry_signature)
    requirements = (await db_session.scalars(select(PaymentRefundRequirement))).all()
    assert len(requirements) == 1
    assert requirements[0].source_type is ExcessCaptureSource.feature_booking
    assert requirements[0].source_reference == str(booking.id)
    assert len((await db_session.scalars(select(LedgerEntry))).all()) == 4


@pytest.mark.asyncio
async def test_failed_featuring_reservation_releases_only_at_original_expiry(db_session):
    admin, _ = await accounts.register(
        db_session, "feature-expiry-admin@example.com", "strong-password-123", None
    )
    owner, profile = await creator(db_session, "feature-expiry-owner@example.com")
    surface = await service.create_surface(db_session, admin, "discover_creators")
    slot = await service.create_slot(db_session, admin, surface.id, "expiry-slot", 0)
    await service.create_price(db_session, admin, slot.id, "creator", 3600, 900, "EUR")
    starts_at = datetime.now(UTC) + timedelta(hours=2)
    booking = await service.create_booking(
        db_session,
        actor=owner,
        purchaser=owner,
        slot_id=slot.id,
        target_type="creator",
        target_id=profile.id,
        starts_at=starts_at,
        duration_seconds=3600,
        idempotency_key="feature-expiry-booking",
    )
    attempt = await service.initiate_payment(db_session, booking, owner, "feature-expiry-payment")
    attempt.status = PaymentStatus.failed
    await service.fail_payment_attempt(db_session, attempt)
    booking.reservation_expires_at = datetime.now(UTC) - timedelta(seconds=1)

    assert await service.expire_reservations(db_session) == 1
    assert booking.status is FeatureBookingStatus.failed
    assert booking.reservation_expires_at is None
    replacement = await service.create_booking(
        db_session,
        actor=owner,
        purchaser=owner,
        slot_id=slot.id,
        target_type="creator",
        target_id=profile.id,
        starts_at=starts_at,
        duration_seconds=3600,
        idempotency_key="feature-expiry-replacement",
    )
    assert replacement.status is FeatureBookingStatus.awaiting_payment


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
    second_reason = await service._refund(
        db_session,
        partial,
        FeatureIneligibilityReason.moderation_ineligible,
        901,
    )
    assert second_reason and second_reason.amount_minor == 451
    assert sum((await db_session.scalars(select(FeatureRefund.amount_minor))).all()) == 1_802
    provider_refund = json.dumps(
        {
            "id": f"partial-provider-refund-{partial_attempt.id}",
            "type": "payment.refunded",
            "payment_reference": partial_attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    provider_refund_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(), provider_refund, hashlib.sha256
    ).hexdigest()
    await finance.process_development_webhook(
        db_session, provider_refund, provider_refund_signature
    )
    assert partial.status is FeatureBookingStatus.refunded
    reversal_debits = await db_session.scalar(
        select(func.sum(LedgerEntry.amount_minor))
        .join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.transaction_id)
        .where(
            LedgerTransaction.reversal_of_transaction_id == partial.ledger_transaction_id,
            LedgerEntry.direction == LedgerDirection.debit,
        )
    )
    assert reversal_debits == 901
    provider_chargeback = json.dumps(
        {
            "id": f"partial-provider-chargeback-{partial_attempt.id}",
            "type": "payment.chargeback",
            "payment_reference": partial_attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    provider_chargeback_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(), provider_chargeback, hashlib.sha256
    ).hexdigest()
    await finance.process_development_webhook(
        db_session, provider_chargeback, provider_chargeback_signature
    )
    assert partial.status is FeatureBookingStatus.chargeback
    assert (
        await db_session.scalar(
            select(func.sum(LedgerEntry.amount_minor))
            .join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.transaction_id)
            .where(
                LedgerTransaction.reversal_of_transaction_id == partial.ledger_transaction_id,
                LedgerEntry.direction == LedgerDirection.debit,
            )
        )
        == 901
    )
    await service.terminate_ineligible(
        db_session, partial, FeatureIneligibilityReason.moderation_ineligible
    )
    assert partial.status is FeatureBookingStatus.chargeback


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
async def test_sponsored_analytics_are_separate_and_render_deduplicated(db_session):
    admin, _ = await accounts.register(
        db_session, "feature-admin-analytics@example.com", "strong-password-123", None
    )
    owner, profile = await creator(db_session, "feature-analytics@example.com")
    viewer, _ = await accounts.register(
        db_session, "feature-analytics-viewer@example.com", "strong-password-123", None
    )
    surface = await service.create_surface(db_session, admin, "discover_creators")
    slot = await service.create_slot(db_session, admin, surface.id, "analytics-1", 0)
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
        idempotency_key="analytics-booking",
    )
    booking.status = FeatureBookingStatus.active
    config = await discovery.current_config(db_session)
    await discovery.record_event(
        db_session,
        event_type="recommendation_impression",
        request_key="organic-render",
        user=viewer,
        ranking_version=config.version,
        entity_type="creator",
        entity_id=profile.id,
    )
    await service.record_sponsored_event(
        db_session,
        event_type="sponsored_impression",
        request_key="sponsored-render",
        user=viewer,
        booking_id=booking.id,
    )
    await service.record_sponsored_event(
        db_session,
        event_type="sponsored_impression",
        request_key="sponsored-render",
        user=viewer,
        booking_id=booking.id,
    )
    await service.record_sponsored_event(
        db_session,
        event_type="sponsored_click",
        request_key="sponsored-click",
        user=viewer,
        booking_id=booking.id,
    )
    await service.record_sponsored_event(
        db_session,
        event_type="sponsored_conversion",
        request_key="sponsored-conversion",
        user=viewer,
        booking_id=booking.id,
    )
    rows = (
        await db_session.scalars(select(DiscoveryEvent).order_by(DiscoveryEvent.event_type))
    ).all()
    assert [row.event_type for row in rows] == [
        "recommendation_impression",
        "sponsored_click",
        "sponsored_conversion",
        "sponsored_impression",
    ]
    sponsored = next(row for row in rows if row.event_type == "sponsored_impression")
    assert sponsored.metadata_json["booking_id"] == str(booking.id)


@pytest.mark.asyncio
async def test_sold_out_marketplace_and_ended_live_fail_closed_with_policy_correct_refunds(
    db_session,
):
    admin, _ = await accounts.register(
        db_session, "feature-admin-targets@example.com", "strong-password-123", None
    )
    owner, profile = await creator(db_session, "feature-targets@example.com")
    listing = MarketplaceListing(
        public_id="listing-featuring-targets",
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        title="Available featured listing",
        description="safe",
        category="prints",
        condition=MarketplaceCondition.new,
        status=MarketplaceListingStatus.published,
        moderation_status=ModerationStatus.approved,
        quantity_available=1,
        price_amount_minor=1000,
        currency="EUR",
        shipping_mode=MarketplaceShippingMode.worldwide,
        origin_country_code="PT",
        published_at=datetime.now(UTC),
    )
    room = LiveRoom(
        creator_id=profile.id,
        public_id="live-featuring-targets",
        provider_room_name="live-featuring-targets-room",
        status=LiveRoomStatus.live,
        access_mode=LiveAccessMode.public,
        title="Live target",
        description="safe",
        started_at=datetime.now(UTC),
    )
    db_session.add_all([listing, room])
    await db_session.flush()
    assert (
        await service.assert_target_eligibility(
            db_session, service.FeatureTargetType.marketplace_listing, listing.id
        )
        == profile.id
    )
    assert (
        await service.assert_target_eligibility(
            db_session, service.FeatureTargetType.live_room, room.id
        )
        == profile.id
    )
    listing.quantity_available = 0
    with pytest.raises(service.FeaturingError, match="not eligible"):
        await service.assert_target_eligibility(
            db_session, service.FeatureTargetType.marketplace_listing, listing.id
        )

    surface = await service.create_surface(db_session, admin, "live_now")
    slot = await service.create_slot(db_session, admin, surface.id, "live-target-1", 0)
    await service.create_price(db_session, admin, slot.id, "live_room", 3600, 900, "EUR")
    booking = await service.create_booking(
        db_session,
        actor=owner,
        purchaser=owner,
        slot_id=slot.id,
        target_type="live_room",
        target_id=room.id,
        starts_at=datetime.now(UTC) + timedelta(hours=2),
        duration_seconds=3600,
        idempotency_key="creator-live-end",
    )
    attempt = await service.initiate_payment(db_session, booking, owner)
    attempt.status = PaymentStatus.succeeded
    await service.settle_payment(db_session, booking)
    booking.status = FeatureBookingStatus.active
    booking.activated_at = datetime.now(UTC) - timedelta(seconds=60)
    room.status = LiveRoomStatus.ended
    await service.terminate_ineligible(
        db_session, booking, FeatureIneligibilityReason.creator_ended
    )
    assert booking.status is FeatureBookingStatus.suspended
    assert (
        await db_session.scalar(select(FeatureRefund).where(FeatureRefund.booking_id == booking.id))
        is None
    )


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


@pytest.mark.asyncio
async def test_cancellation_and_termination_serialize_with_provider_callbacks(
    db_session, monkeypatch
):
    admin, _ = await accounts.register(
        db_session, "feature-lock-admin@example.com", "strong-password-123", None
    )
    owner, profile = await creator(db_session, "feature-lock-owner@example.com")
    surface = await service.create_surface(
        db_session, admin, "discover_creators", cancellation_cutoff_seconds=60
    )
    slot = await service.create_slot(db_session, admin, surface.id, "feature-lock-slot", 0)
    await service.create_price(db_session, admin, slot.id, "creator", 3600, 900, "EUR")
    starts_at = datetime.now(UTC) + timedelta(hours=3)

    cancel_first = await service.create_booking(
        db_session,
        actor=owner,
        purchaser=owner,
        slot_id=slot.id,
        target_type="creator",
        target_id=profile.id,
        starts_at=starts_at,
        duration_seconds=3600,
        idempotency_key="feature-cancel-first-race",
    )
    cancel_first_attempt = await service.initiate_payment(db_session, cancel_first, owner)
    cancel_first_success, cancel_first_signature = finance.development_webhook_payload(
        cancel_first_attempt
    )

    provider_first = await service.create_booking(
        db_session,
        actor=owner,
        purchaser=owner,
        slot_id=slot.id,
        target_type="creator",
        target_id=profile.id,
        starts_at=starts_at + timedelta(hours=2),
        duration_seconds=3600,
        idempotency_key="feature-provider-first-race",
    )
    provider_first_attempt = await service.initiate_payment(db_session, provider_first, owner)
    provider_first_success, provider_first_signature = finance.development_webhook_payload(
        provider_first_attempt
    )

    terminate_first = await service.create_booking(
        db_session,
        actor=owner,
        purchaser=owner,
        slot_id=slot.id,
        target_type="creator",
        target_id=profile.id,
        starts_at=starts_at + timedelta(hours=4),
        duration_seconds=3600,
        idempotency_key="feature-terminate-first-race",
    )
    terminate_attempt = await service.initiate_payment(db_session, terminate_first, owner)
    terminate_attempt.status = PaymentStatus.succeeded
    terminate_attempt.completed_at = datetime.now(UTC)
    await service.settle_payment(db_session, terminate_first, terminate_attempt)
    terminate_first.status = FeatureBookingStatus.active
    terminate_first.activated_at = datetime.now(UTC)
    terminate_first.ends_at = terminate_first.activated_at + timedelta(seconds=3600)
    terminate_chargeback, terminate_chargeback_signature = signed_payment_event(
        terminate_attempt,
        "payment.chargeback",
        f"feature-terminate-chargeback-{terminate_attempt.id}",
    )
    await db_session.commit()

    async def run_webhook(payload: bytes, signature: str) -> None:
        async with SessionLocal() as session:
            await finance.process_development_webhook(session, payload, signature)
            await session.commit()

    async def run_cancel(booking_id, actor_id, now) -> None:
        async with SessionLocal() as session:
            booking = await session.get(FeatureBooking, booking_id)
            actor = await session.get(User, actor_id)
            assert booking and actor
            await service.cancel_before_start(session, booking, actor, now=now)
            await session.commit()

    original_record_event = service.record_event
    cancel_locked = asyncio.Event()
    allow_cancel = asyncio.Event()

    async def pause_cancel_after_lock(db, event_type, **kwargs):
        if (
            event_type == "featuring.booking_cancelled"
            and kwargs.get("target_id") == str(cancel_first.id)
            and not cancel_locked.is_set()
        ):
            cancel_locked.set()
            await asyncio.wait_for(allow_cancel.wait(), timeout=5)
        return await original_record_event(db, event_type, **kwargs)

    monkeypatch.setattr(service, "record_event", pause_cancel_after_lock)
    cancel_task = asyncio.create_task(
        run_cancel(cancel_first.id, owner.id, starts_at - timedelta(hours=1))
    )
    await asyncio.wait_for(cancel_locked.wait(), timeout=5)
    late_success_task = asyncio.create_task(
        run_webhook(cancel_first_success, cancel_first_signature)
    )
    await asyncio.sleep(0.05)
    assert not late_success_task.done()
    allow_cancel.set()
    await asyncio.wait_for(cancel_task, timeout=5)
    await asyncio.wait_for(late_success_task, timeout=5)

    async with SessionLocal() as verification:
        booking = await verification.get(FeatureBooking, cancel_first.id)
        attempt = await verification.get(PaymentAttempt, cancel_first_attempt.id)
        assert booking and booking.status is FeatureBookingStatus.cancelled
        assert booking.ledger_transaction_id is None
        assert attempt and attempt.status is PaymentStatus.succeeded
        assert await verification.scalar(
            select(PaymentRefundRequirement.id).where(
                PaymentRefundRequirement.payment_attempt_id == cancel_first_attempt.id
            )
        )

    monkeypatch.setattr(service, "record_event", original_record_event)
    original_settle_attempt = service.settle_payment_attempt
    provider_locked = asyncio.Event()
    allow_provider = asyncio.Event()

    async def pause_provider_after_settlement(db, attempt):
        result = await original_settle_attempt(db, attempt)
        if attempt.id == provider_first_attempt.id and not provider_locked.is_set():
            provider_locked.set()
            await asyncio.wait_for(allow_provider.wait(), timeout=5)
        return result

    monkeypatch.setattr(service, "settle_payment_attempt", pause_provider_after_settlement)
    provider_task = asyncio.create_task(
        run_webhook(provider_first_success, provider_first_signature)
    )
    await asyncio.wait_for(provider_locked.wait(), timeout=5)
    provider_cancel_task = asyncio.create_task(
        run_cancel(
            provider_first.id,
            owner.id,
            provider_first.starts_at - timedelta(hours=1),
        )
    )
    await asyncio.sleep(0.05)
    assert not provider_cancel_task.done()
    allow_provider.set()
    await asyncio.wait_for(provider_task, timeout=5)
    await asyncio.wait_for(provider_cancel_task, timeout=5)

    async with SessionLocal() as verification:
        booking = await verification.get(FeatureBooking, provider_first.id)
        assert booking and booking.status is FeatureBookingStatus.refunded
        assert booking.ledger_transaction_id
        assert (
            await verification.scalar(
                select(func.count())
                .select_from(LedgerTransaction)
                .where(
                    LedgerTransaction.reversal_of_transaction_id == booking.ledger_transaction_id
                )
            )
            == 1
        )

    monkeypatch.setattr(service, "settle_payment_attempt", original_settle_attempt)
    terminate_locked = asyncio.Event()
    allow_terminate = asyncio.Event()

    async def pause_termination_after_refund(db, event_type, **kwargs):
        if (
            event_type == "featuring.eligibility_failure"
            and kwargs.get("target_id") == str(terminate_first.id)
            and not terminate_locked.is_set()
        ):
            terminate_locked.set()
            await asyncio.wait_for(allow_terminate.wait(), timeout=5)
        return await original_record_event(db, event_type, **kwargs)

    monkeypatch.setattr(service, "record_event", pause_termination_after_refund)

    async def run_termination() -> None:
        async with SessionLocal() as session:
            booking = await session.get(FeatureBooking, terminate_first.id)
            assert booking
            await service.terminate_ineligible(
                session,
                booking,
                FeatureIneligibilityReason.platform_failure,
                now=terminate_first.activated_at,
            )
            await session.commit()

    termination_task = asyncio.create_task(run_termination())
    await asyncio.wait_for(terminate_locked.wait(), timeout=5)
    termination_chargeback_task = asyncio.create_task(
        run_webhook(terminate_chargeback, terminate_chargeback_signature)
    )
    await asyncio.sleep(0.05)
    assert not termination_chargeback_task.done()
    allow_terminate.set()
    await asyncio.wait_for(termination_task, timeout=5)
    await asyncio.wait_for(termination_chargeback_task, timeout=5)

    async with SessionLocal() as verification:
        booking = await verification.get(FeatureBooking, terminate_first.id)
        attempt = await verification.get(PaymentAttempt, terminate_attempt.id)
        assert booking and booking.status is FeatureBookingStatus.chargeback
        assert attempt and attempt.status is PaymentStatus.chargeback
        assert (
            await verification.scalar(
                select(func.count())
                .select_from(LedgerTransaction)
                .where(
                    LedgerTransaction.reversal_of_transaction_id == booking.ledger_transaction_id
                )
            )
            == 1
        )
