import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamped, UUIDPrimaryKey


class SubscriptionDuration(str, enum.Enum):
    month_1 = "month_1"
    month_3 = "month_3"
    month_6 = "month_6"
    month_12 = "month_12"


class PromotionEligibility(str, enum.Enum):
    new_subscriber = "new_subscriber"
    all_eligible = "all_eligible"
    reactivation = "reactivation"


class PromotionRenewalScope(str, enum.Enum):
    initial_only = "initial_only"
    initial_and_renewal = "initial_and_renewal"


class SubscriptionStatus(str, enum.Enum):
    pending = "pending"
    active = "active"
    grace_period = "grace_period"
    payment_failed = "payment_failed"
    expired = "expired"
    suspended = "suspended"


class SubscriptionPeriodStatus(str, enum.Enum):
    pending = "pending"
    active = "active"
    failed = "failed"
    refunded = "refunded"


class SubscriptionPlan(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "subscription_plans"
    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), unique=True, index=True
    )
    currency: Mapped[str] = mapped_column(String(3))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class SubscriptionPlanPrice(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "subscription_plan_prices"
    __table_args__ = (
        UniqueConstraint("plan_id", "duration", name="uq_subscription_plan_duration"),
        CheckConstraint("amount_minor > 0", name="ck_subscription_plan_positive_price"),
    )
    plan_id: Mapped[UUID] = mapped_column(
        ForeignKey("subscription_plans.id", ondelete="CASCADE"), index=True
    )
    duration: Mapped[SubscriptionDuration] = mapped_column(
        Enum(SubscriptionDuration, name="subscription_duration")
    )
    amount_minor: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class SubscriptionPromotion(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "subscription_promotions"
    __table_args__ = (
        CheckConstraint(
            "end_at IS NULL OR end_at > start_at", name="ck_subscription_promotion_dates"
        ),
    )
    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    eligibility: Mapped[PromotionEligibility] = mapped_column(
        Enum(PromotionEligibility, name="promotion_eligibility")
    )
    renewal_scope: Mapped[PromotionRenewalScope] = mapped_column(
        Enum(PromotionRenewalScope, name="promotion_renewal_scope"),
        default=PromotionRenewalScope.initial_only,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SubscriptionPromotionRule(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "subscription_promotion_rules"
    __table_args__ = (
        UniqueConstraint("promotion_id", "duration", name="uq_subscription_promotion_duration"),
        CheckConstraint(
            "discount_basis_points >= 0 AND discount_basis_points < 10000",
            name="ck_subscription_promotion_discount",
        ),
    )
    promotion_id: Mapped[UUID] = mapped_column(
        ForeignKey("subscription_promotions.id", ondelete="CASCADE"), index=True
    )
    duration: Mapped[SubscriptionDuration] = mapped_column(
        Enum(SubscriptionDuration, name="subscription_duration", create_type=False)
    )
    discount_basis_points: Mapped[int] = mapped_column(Integer)


class Subscription(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "subscriptions"
    __table_args__ = (Index("ix_subscription_due", "status", "current_period_end"),)
    subscriber_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    plan_id: Mapped[UUID] = mapped_column(ForeignKey("subscription_plans.id", ondelete="RESTRICT"))
    duration: Mapped[SubscriptionDuration] = mapped_column(
        Enum(SubscriptionDuration, name="subscription_duration", create_type=False)
    )
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(SubscriptionStatus, name="subscription_status"),
        default=SubscriptionStatus.pending,
        index=True,
    )
    auto_renew: Mapped[bool] = mapped_column(Boolean, default=True)
    cancel_at_period_end: Mapped[bool] = mapped_column(Boolean, default=False)
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    grace_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SubscriptionPeriod(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "subscription_periods"
    __table_args__ = (
        UniqueConstraint("subscription_id", "sequence", name="uq_subscription_period_sequence"),
        CheckConstraint(
            "base_amount_minor > 0 AND charged_amount_minor > 0",
            name="ck_subscription_period_positive_amounts",
        ),
        CheckConstraint(
            "base_amount_minor = discount_amount_minor + charged_amount_minor",
            name="ck_subscription_period_discount_balance",
        ),
    )
    subscription_id: Mapped[UUID] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="RESTRICT"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    status: Mapped[SubscriptionPeriodStatus] = mapped_column(
        Enum(SubscriptionPeriodStatus, name="subscription_period_status"),
        default=SubscriptionPeriodStatus.pending,
        index=True,
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    duration: Mapped[SubscriptionDuration] = mapped_column(
        Enum(SubscriptionDuration, name="subscription_duration", create_type=False)
    )
    base_amount_minor: Mapped[int] = mapped_column(Integer)
    discount_amount_minor: Mapped[int] = mapped_column(Integer, default=0)
    charged_amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    promotion_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("subscription_promotions.id", ondelete="RESTRICT")
    )
    promotion_eligibility: Mapped[PromotionEligibility | None] = mapped_column(
        Enum(PromotionEligibility, name="promotion_eligibility", create_type=False)
    )
    discount_basis_points: Mapped[int] = mapped_column(Integer, default=0)
    commission_basis_points: Mapped[int] = mapped_column(Integer)
    platform_fee_minor: Mapped[int] = mapped_column(Integer)
    creator_amount_minor: Mapped[int] = mapped_column(Integer)
    payment_attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="RESTRICT"), unique=True
    )
    ledger_transaction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), unique=True
    )
    entitlement_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("content_entitlements.id", ondelete="RESTRICT"), unique=True
    )


class SubscriptionRenewalAttempt(UUIDPrimaryKey, Timestamped, Base):
    """Durable payment-attempt history for one renewal period."""

    __tablename__ = "subscription_renewal_attempts"
    __table_args__ = (
        UniqueConstraint(
            "subscription_period_id", "attempt_number", name="uq_renewal_attempt_number"
        ),
        UniqueConstraint("payment_attempt_id", name="uq_renewal_attempt_payment"),
    )
    subscription_period_id: Mapped[UUID] = mapped_column(
        ForeignKey("subscription_periods.id", ondelete="RESTRICT"), index=True
    )
    payment_attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="RESTRICT"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
