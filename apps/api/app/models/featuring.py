"""Server-authoritative paid featuring inventory and booking state."""

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamped, UUIDPrimaryKey


class FeatureSurfaceKind(str, enum.Enum):
    discover_home_hero = "discover_home_hero"
    discover_creators = "discover_creators"
    discover_content = "discover_content"
    live_now = "live_now"
    marketplace = "marketplace"
    creator_search = "creator_search"
    content_search = "content_search"


class FeatureSurfaceStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    disabled = "disabled"


class FeatureTargetType(str, enum.Enum):
    creator = "creator"
    post = "post"
    video = "video"
    gallery = "gallery"
    marketplace_listing = "marketplace_listing"
    live_room = "live_room"


class FeatureBookingStatus(str, enum.Enum):
    awaiting_payment = "awaiting_payment"
    scheduled = "scheduled"
    active = "active"
    completed = "completed"
    cancelled = "cancelled"
    refunded = "refunded"
    failed = "failed"
    suspended = "suspended"
    chargeback = "chargeback"


class FeatureIneligibilityReason(str, enum.Enum):
    creator_ended = "creator_ended"
    platform_failure = "platform_failure"
    moderation_ineligible = "moderation_ineligible"
    admin_disabled = "admin_disabled"


class FeatureSurface(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "feature_surfaces"
    kind: Mapped[FeatureSurfaceKind] = mapped_column(
        Enum(FeatureSurfaceKind, name="feature_surface_kind"), unique=True, index=True
    )
    status: Mapped[FeatureSurfaceStatus] = mapped_column(
        Enum(FeatureSurfaceStatus, name="feature_surface_status"),
        default=FeatureSurfaceStatus.active,
        index=True,
    )
    cancellation_cutoff_seconds: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)


class FeatureSlot(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "feature_slots"
    __table_args__ = (
        UniqueConstraint("surface_id", "slot_key", name="uq_feature_slot_surface_key"),
        CheckConstraint("position >= 0", name="ck_feature_slot_position"),
        CheckConstraint("capacity > 0", name="ck_feature_slot_capacity"),
    )
    surface_id: Mapped[UUID] = mapped_column(
        ForeignKey("feature_surfaces.id", ondelete="RESTRICT"), index=True
    )
    slot_key: Mapped[str] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(Integer)
    capacity: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class FeaturePrice(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "feature_prices"
    __table_args__ = (
        UniqueConstraint(
            "slot_id", "target_type", "duration_seconds", "version", name="uq_feature_price_version"
        ),
        CheckConstraint("amount_minor > 0", name="ck_feature_price_positive"),
        CheckConstraint("duration_seconds > 0", name="ck_feature_price_duration"),
    )
    slot_id: Mapped[UUID] = mapped_column(
        ForeignKey("feature_slots.id", ondelete="RESTRICT"), index=True
    )
    target_type: Mapped[FeatureTargetType] = mapped_column(
        Enum(FeatureTargetType, name="feature_target_type"), index=True
    )
    duration_seconds: Mapped[int] = mapped_column(Integer)
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    version: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class FeatureBooking(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "feature_bookings"
    __table_args__ = (
        UniqueConstraint(
            "purchaser_user_id", "idempotency_key", name="uq_feature_booking_idempotency"
        ),
        CheckConstraint("price_minor > 0", name="ck_feature_booking_price"),
        CheckConstraint("duration_seconds > 0", name="ck_feature_booking_duration"),
    )
    public_id: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    purchaser_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    actor_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    owner_creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    surface_id: Mapped[UUID] = mapped_column(
        ForeignKey("feature_surfaces.id", ondelete="RESTRICT"), index=True
    )
    slot_id: Mapped[UUID] = mapped_column(
        ForeignKey("feature_slots.id", ondelete="RESTRICT"), index=True
    )
    target_type: Mapped[FeatureTargetType] = mapped_column(
        Enum(FeatureTargetType, name="feature_target_type", create_type=False), index=True
    )
    target_id: Mapped[UUID] = mapped_column(index=True)
    status: Mapped[FeatureBookingStatus] = mapped_column(
        Enum(FeatureBookingStatus, name="feature_booking_status"),
        default=FeatureBookingStatus.awaiting_payment,
        index=True,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_seconds: Mapped[int] = mapped_column(Integer)
    price_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    price_version: Mapped[int] = mapped_column(Integer)
    cancellation_cutoff_seconds: Mapped[int] = mapped_column(Integer)
    payment_attempt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="RESTRICT"), unique=True
    )
    ledger_transaction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), unique=True
    )
    reservation_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ineligibility_reason: Mapped[FeatureIneligibilityReason | None] = mapped_column(
        Enum(FeatureIneligibilityReason, name="feature_ineligibility_reason")
    )
    delivered_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128))


class FeatureBookingPaymentAttempt(UUIDPrimaryKey, Timestamped, Base):
    """Durable payment-attempt history for one immutable featuring booking."""

    __tablename__ = "feature_booking_payment_attempts"
    __table_args__ = (
        CheckConstraint("attempt_number > 0", name="ck_feature_booking_attempt_positive_number"),
        UniqueConstraint("booking_id", "attempt_number", name="uq_feature_booking_attempt_number"),
        UniqueConstraint("payment_attempt_id", name="uq_feature_booking_attempt_payment"),
    )
    booking_id: Mapped[UUID] = mapped_column(
        ForeignKey("feature_bookings.id", ondelete="RESTRICT"), index=True
    )
    payment_attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="RESTRICT"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer)


class FeatureRefund(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "feature_refunds"
    __table_args__ = (
        UniqueConstraint("booking_id", "reason", name="uq_feature_refund_booking_reason"),
        CheckConstraint("amount_minor > 0", name="ck_feature_refund_positive"),
    )
    booking_id: Mapped[UUID] = mapped_column(
        ForeignKey("feature_bookings.id", ondelete="RESTRICT"), index=True
    )
    reason: Mapped[FeatureIneligibilityReason] = mapped_column(
        Enum(FeatureIneligibilityReason, name="feature_ineligibility_reason", create_type=False)
    )
    amount_minor: Mapped[int] = mapped_column(Integer)
    ledger_transaction_id: Mapped[UUID] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), unique=True
    )
