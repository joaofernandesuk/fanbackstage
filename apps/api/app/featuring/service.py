"""Paid featuring commands; organic discovery scoring is deliberately not imported or changed here."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.core.config import get_settings
from app.finance.service import _account, currency_code, post_entries
from app.groups.service import has_delegated_permission
from app.models.content import ContentItem, ContentStatus, ModerationStatus
from app.models.creator import CreatorProfile, CreatorStatus
from app.models.featuring import (
    FeatureBooking,
    FeatureBookingStatus,
    FeaturePrice,
    FeatureSlot,
    FeatureSurface,
    FeatureSurfaceStatus,
    FeatureTargetType,
)
from app.models.finance import (
    LedgerAccountKind,
    LedgerDirection,
    LedgerTransactionType,
    PaymentAttempt,
    PaymentStatus,
)
from app.models.groups import GroupPermission
from app.models.identity import User
from app.models.marketplace import MarketplaceListing, MarketplaceListingStatus
from app.models.social import FeedPost, FeedPostStatus
from app.models.streaming import LiveRoom, LiveRoomStatus

RESERVATION_SECONDS = 15 * 60
INELIGIBLE_MODERATION = (
    ModerationStatus.flagged,
    ModerationStatus.rejected,
    ModerationStatus.removed,
)


class FeaturingError(ValueError):
    pass


async def _owner_and_eligible(
    db: AsyncSession, target_type: FeatureTargetType, target_id: UUID
) -> tuple[UUID, bool]:
    if target_type is FeatureTargetType.creator:
        target = await db.get(CreatorProfile, target_id)
        return (
            (
                target.id,
                bool(target and target.status is CreatorStatus.approved and target.is_public),
            )
            if target
            else (UUID(int=0), False)
        )
    if target_type in {FeatureTargetType.video, FeatureTargetType.gallery}:
        target = await db.get(ContentItem, target_id)
        if not target:
            return UUID(int=0), False
        valid_type = target.content_type.value == target_type.value
        return target.owner_creator_id, bool(
            valid_type
            and target.status is ContentStatus.published
            and target.moderation_status not in INELIGIBLE_MODERATION
        )
    if target_type is FeatureTargetType.post:
        target = await db.get(FeedPost, target_id)
        if not target:
            return UUID(int=0), False
        return target.creator_id, bool(
            target.status is FeedPostStatus.published
            and target.moderation_status not in INELIGIBLE_MODERATION
        )
    if target_type is FeatureTargetType.marketplace_listing:
        target = await db.get(MarketplaceListing, target_id)
        if not target:
            return UUID(int=0), False
        return target.owner_creator_id, bool(
            target.status is MarketplaceListingStatus.published
            and target.quantity_available > 0
            and target.moderation_status not in INELIGIBLE_MODERATION
        )
    if target_type is FeatureTargetType.live_room:
        target = await db.get(LiveRoom, target_id)
        if not target:
            return UUID(int=0), False
        return target.creator_id, target.status is LiveRoomStatus.live
    raise FeaturingError("Unsupported featuring target")


async def assert_target_eligibility(
    db: AsyncSession, target_type: FeatureTargetType, target_id: UUID
) -> UUID:
    creator_id, eligible = await _owner_and_eligible(db, target_type, target_id)
    creator = await db.get(CreatorProfile, creator_id) if eligible else None
    if (
        not eligible
        or not creator
        or creator.status is not CreatorStatus.approved
        or not creator.is_public
    ):
        raise FeaturingError("Target is not eligible for featuring")
    return creator_id


async def assert_actor_can_feature(db: AsyncSession, actor: User, creator: CreatorProfile) -> None:
    if actor.id == creator.user_id:
        return
    if not await has_delegated_permission(
        db, actor.id, creator.id, GroupPermission.manage_featuring
    ):
        raise FeaturingError("You are not authorized to feature this target")


async def create_surface(
    db: AsyncSession, actor: User, kind: str, cancellation_cutoff_seconds: int = 3600
) -> FeatureSurface:
    if cancellation_cutoff_seconds < 0 or cancellation_cutoff_seconds > 30 * 24 * 3600:
        raise FeaturingError("Invalid cancellation cutoff")
    from app.models.featuring import FeatureSurfaceKind

    existing = await db.scalar(
        select(FeatureSurface).where(FeatureSurface.kind == FeatureSurfaceKind(kind))
    )
    if existing:
        return existing
    row = FeatureSurface(
        kind=FeatureSurfaceKind(kind), cancellation_cutoff_seconds=cancellation_cutoff_seconds
    )
    db.add(row)
    await db.flush()
    await record_event(
        db,
        "featuring.surface_created",
        actor_user_id=actor.id,
        target_type="feature_surface",
        target_id=str(row.id),
        metadata={"kind": kind},
    )
    return row


async def create_slot(
    db: AsyncSession, actor: User, surface_id: UUID, slot_key: str, position: int, capacity: int = 1
) -> FeatureSlot:
    if not slot_key or len(slot_key) > 64 or position < 0 or capacity < 1:
        raise FeaturingError("Invalid feature slot")
    if not await db.get(FeatureSurface, surface_id):
        raise FeaturingError("Feature surface not found")
    row = FeatureSlot(
        surface_id=surface_id, slot_key=slot_key, position=position, capacity=capacity
    )
    db.add(row)
    await db.flush()
    await record_event(
        db,
        "featuring.slot_created",
        actor_user_id=actor.id,
        target_type="feature_slot",
        target_id=str(row.id),
        metadata={"surface_id": str(surface_id)},
    )
    return row


async def create_price(
    db: AsyncSession,
    actor: User,
    slot_id: UUID,
    target_type: str,
    duration_seconds: int,
    amount_minor: int,
    currency: str,
) -> FeaturePrice:
    if duration_seconds <= 0 or amount_minor <= 0:
        raise FeaturingError("Invalid feature price")
    kind = FeatureTargetType(target_type)
    slot = await db.get(FeatureSlot, slot_id)
    if not slot:
        raise FeaturingError("Feature slot not found")
    previous = await db.scalar(
        select(func.max(FeaturePrice.version)).where(
            FeaturePrice.slot_id == slot_id,
            FeaturePrice.target_type == kind,
            FeaturePrice.duration_seconds == duration_seconds,
        )
    )
    row = FeaturePrice(
        slot_id=slot_id,
        target_type=kind,
        duration_seconds=duration_seconds,
        amount_minor=amount_minor,
        currency=currency_code(currency),
        version=(previous or 0) + 1,
    )
    db.add(row)
    await db.flush()
    await record_event(
        db,
        "featuring.price_created",
        actor_user_id=actor.id,
        target_type="feature_price",
        target_id=str(row.id),
        metadata={"version": row.version},
    )
    return row


async def _availability_count(
    db: AsyncSession, slot_id: UUID, starts_at: datetime, ends_at: datetime, now: datetime
) -> int:
    return int(
        (
            await db.scalar(
                select(func.count(FeatureBooking.id)).where(
                    FeatureBooking.slot_id == slot_id,
                    FeatureBooking.starts_at < ends_at,
                    FeatureBooking.ends_at > starts_at,
                    (
                        (
                            FeatureBooking.status.in_(
                                [FeatureBookingStatus.scheduled, FeatureBookingStatus.active]
                            )
                        )
                        | (
                            (FeatureBooking.status == FeatureBookingStatus.awaiting_payment)
                            & (FeatureBooking.reservation_expires_at > now)
                        )
                    ),
                )
            )
        )
        or 0
    )


async def create_booking(
    db: AsyncSession,
    *,
    actor: User,
    purchaser: User,
    slot_id: UUID,
    target_type: str,
    target_id: UUID,
    starts_at: datetime,
    duration_seconds: int,
    idempotency_key: str,
) -> FeatureBooking:
    if (
        not idempotency_key
        or len(idempotency_key) > 128
        or duration_seconds <= 0
        or starts_at.tzinfo is None
    ):
        raise FeaturingError("Invalid booking request")
    existing = await db.scalar(
        select(FeatureBooking).where(
            FeatureBooking.purchaser_user_id == purchaser.id,
            FeatureBooking.idempotency_key == idempotency_key,
        )
    )
    if existing:
        return existing
    slot = await db.scalar(select(FeatureSlot).where(FeatureSlot.id == slot_id).with_for_update())
    if not slot or not slot.active:
        raise FeaturingError("Feature slot is unavailable")
    surface = await db.get(FeatureSurface, slot.surface_id)
    if not surface or surface.status is not FeatureSurfaceStatus.active:
        raise FeaturingError("Feature surface is unavailable")
    kind = FeatureTargetType(target_type)
    owner_creator_id = await assert_target_eligibility(db, kind, target_id)
    creator = await db.get(CreatorProfile, owner_creator_id)
    assert creator
    await assert_actor_can_feature(db, actor, creator)
    # A manager may prepare a booking with a selected payer, but only that payer can start payment.
    if actor.id != creator.user_id and purchaser.id == creator.user_id:
        pass
    ends_at = starts_at.astimezone(UTC) + timedelta(seconds=duration_seconds)
    now = datetime.now(UTC)
    if starts_at.astimezone(UTC) < now:
        raise FeaturingError("Booking start must be in the future")
    if await _availability_count(db, slot.id, starts_at, ends_at, now) >= slot.capacity:
        raise FeaturingError("Feature slot is no longer available")
    price = await db.scalar(
        select(FeaturePrice)
        .where(
            FeaturePrice.slot_id == slot.id,
            FeaturePrice.target_type == kind,
            FeaturePrice.duration_seconds == duration_seconds,
            FeaturePrice.active.is_(True),
        )
        .order_by(FeaturePrice.version.desc())
    )
    if not price:
        raise FeaturingError("No active price for this feature slot and duration")
    booking = FeatureBooking(
        public_id=f"ft_{secrets.token_urlsafe(14)}",
        purchaser_user_id=purchaser.id,
        actor_user_id=actor.id,
        owner_creator_id=owner_creator_id,
        surface_id=surface.id,
        slot_id=slot.id,
        target_type=kind,
        target_id=target_id,
        starts_at=starts_at.astimezone(UTC),
        ends_at=ends_at,
        duration_seconds=duration_seconds,
        price_minor=price.amount_minor,
        currency=price.currency,
        price_version=price.version,
        cancellation_cutoff_seconds=surface.cancellation_cutoff_seconds,
        reservation_expires_at=now + timedelta(seconds=RESERVATION_SECONDS),
        idempotency_key=idempotency_key,
    )
    db.add(booking)
    await db.flush()
    await record_event(
        db,
        "featuring.booking_created",
        actor_user_id=actor.id,
        target_type="feature_booking",
        target_id=str(booking.id),
        metadata={"payer_user_id": str(purchaser.id), "slot_id": str(slot.id)},
    )
    return booking


async def initiate_payment(
    db: AsyncSession, booking: FeatureBooking, payer: User
) -> PaymentAttempt:
    if booking.purchaser_user_id != payer.id:
        raise FeaturingError("Only the selected payer can authorize this booking")
    if (
        booking.status is not FeatureBookingStatus.awaiting_payment
        or not booking.reservation_expires_at
        or booking.reservation_expires_at <= datetime.now(UTC)
    ):
        raise FeaturingError("Booking payment reservation has expired")
    if booking.payment_attempt_id:
        attempt = await db.get(PaymentAttempt, booking.payment_attempt_id)
        if attempt:
            return attempt
    attempt = PaymentAttempt(
        buyer_user_id=payer.id,
        provider=get_settings().payment_provider,
        provider_reference=f"devpay_{secrets.token_urlsafe(18)}",
        amount_minor=booking.price_minor,
        currency=booking.currency,
        idempotency_key=f"feature:{booking.id}",
    )
    db.add(attempt)
    await db.flush()
    booking.payment_attempt_id = attempt.id
    return attempt


async def settle_payment(db: AsyncSession, booking: FeatureBooking) -> FeatureBooking:
    if booking.status in {
        FeatureBookingStatus.scheduled,
        FeatureBookingStatus.active,
        FeatureBookingStatus.completed,
    }:
        return booking
    if not booking.payment_attempt_id:
        raise FeaturingError("Booking has no payment attempt")
    attempt = await db.get(PaymentAttempt, booking.payment_attempt_id)
    if not attempt or attempt.status is not PaymentStatus.succeeded:
        raise FeaturingError("Booking payment is not settled")
    clearing = await _account(db, LedgerAccountKind.platform_clearing, booking.currency)
    revenue = await _account(db, LedgerAccountKind.platform_revenue, booking.currency)
    ledger = await post_entries(
        db,
        transaction_type=LedgerTransactionType.featuring_charge,
        currency=booking.currency,
        idempotency_key=f"feature-booking:{booking.id}",
        reference=f"feature_booking:{booking.id}",
        entries=[
            (clearing, LedgerDirection.debit, booking.price_minor),
            (revenue, LedgerDirection.credit, booking.price_minor),
        ],
        metadata={
            "booking_id": str(booking.id),
            "payer_user_id": str(booking.purchaser_user_id),
            "owner_creator_id": str(booking.owner_creator_id),
            "price_version": str(booking.price_version),
        },
    )
    booking.ledger_transaction_id = ledger.id
    booking.status = FeatureBookingStatus.scheduled
    booking.reservation_expires_at = None
    await record_event(
        db,
        "featuring.payment_settled",
        actor_user_id=booking.purchaser_user_id,
        target_type="feature_booking",
        target_id=str(booking.id),
        metadata={"ledger_transaction_id": str(ledger.id)},
    )
    return booking
