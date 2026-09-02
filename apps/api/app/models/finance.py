import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamped, UUIDPrimaryKey


class LedgerAccountKind(str, enum.Enum):
    platform_clearing = "platform_clearing"
    platform_revenue = "platform_revenue"
    creator_pending = "creator_pending"
    creator_available = "creator_available"
    group_pending = "group_pending"
    group_available = "group_available"
    referrer_pending = "referrer_pending"
    referrer_available = "referrer_available"
    affiliate_pending = "affiliate_pending"
    affiliate_available = "affiliate_available"
    refund_clearing = "refund_clearing"


class LedgerDirection(str, enum.Enum):
    debit = "debit"
    credit = "credit"


class LedgerTransactionType(str, enum.Enum):
    ppv_purchase = "ppv_purchase"
    earnings_release = "earnings_release"
    refund = "refund"
    chargeback = "chargeback"
    subscription_charge = "subscription_charge"
    messaging_charge = "messaging_charge"
    private_live_session = "private_live_session"
    live_tip = "live_tip"
    live_gift = "live_gift"
    live_paid_request = "live_paid_request"
    marketplace_order = "marketplace_order"
    featuring_charge = "featuring_charge"
    excess_capture_liability = "excess_capture_liability"
    payment_dispute_hold = "payment_dispute_hold"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    succeeded = "succeeded"
    failed = "failed"
    refunded = "refunded"
    disputed = "disputed"
    chargeback = "chargeback"


class SandboxEventStatus(str, enum.Enum):
    """Delivery state for the staging-only external-provider simulator."""

    pending = "pending"
    delivered = "delivered"


class PurchaseStatus(str, enum.Enum):
    awaiting_payment = "awaiting_payment"
    paid = "paid"
    failed = "failed"
    refunded = "refunded"
    disputed = "disputed"
    chargeback = "chargeback"


class RefundRequirementStatus(str, enum.Enum):
    required = "required"
    completed = "completed"


class ExcessCaptureSource(str, enum.Enum):
    ppv_purchase = "ppv_purchase"
    subscription_period = "subscription_period"
    marketplace_order = "marketplace_order"
    feature_booking = "feature_booking"
    message_unlock = "message_unlock"
    paid_message_send = "paid_message_send"
    private_live_session = "private_live_session"
    live_paid_request = "live_paid_request"


class LedgerAccount(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "ledger_accounts"
    __table_args__ = (
        UniqueConstraint(
            "owner_creator_id", "kind", "currency", name="uq_ledger_account_owner_kind_currency"
        ),
        Index(
            "uq_ledger_platform_account_kind_currency",
            "kind",
            "currency",
            unique=True,
            postgresql_where=(
                "owner_creator_id IS NULL AND owner_group_id IS NULL "
                "AND owner_user_id IS NULL AND owner_affiliate_partner_id IS NULL"
            ),
        ),
    )
    owner_creator_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    owner_group_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("groups.id", ondelete="RESTRICT"), index=True
    )
    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    owner_affiliate_partner_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("affiliate_partners.id", ondelete="RESTRICT"), index=True
    )
    kind: Mapped[LedgerAccountKind] = mapped_column(
        Enum(LedgerAccountKind, name="ledger_account_kind"), index=True
    )
    currency: Mapped[str] = mapped_column(String(3))


class LedgerTransaction(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "ledger_transactions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_ledger_transactions_idempotency"),
        Index("ix_ledger_transactions_effective_at", "effective_at"),
    )
    transaction_type: Mapped[LedgerTransactionType] = mapped_column(
        Enum(LedgerTransactionType, name="ledger_transaction_type"), index=True
    )
    currency: Mapped[str] = mapped_column(String(3))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    reference: Mapped[str] = mapped_column(String(255), unique=True)
    reversal_of_transaction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT")
    )
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class LedgerEntry(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_ledger_entries_positive_amount"),
    )
    transaction_id: Mapped[UUID] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), index=True
    )
    ledger_account_id: Mapped[UUID] = mapped_column(
        ForeignKey("ledger_accounts.id", ondelete="RESTRICT"), index=True
    )
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    direction: Mapped[LedgerDirection] = mapped_column(
        Enum(LedgerDirection, name="ledger_direction")
    )


class CommissionRule(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "commission_rules"
    __table_args__ = (
        CheckConstraint(
            "basis_points >= 0 AND basis_points <= 10000", name="ck_commission_rule_bps"
        ),
    )
    revenue_type: Mapped[str] = mapped_column(String(64), default="ppv", unique=True)
    basis_points: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(default=True)


class PaymentAttempt(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "payment_attempts"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_payment_attempt_positive_amount"),
        UniqueConstraint(
            "provider", "provider_reference", name="uq_payment_attempt_provider_reference"
        ),
        UniqueConstraint(
            "buyer_user_id", "idempotency_key", name="uq_payment_attempt_buyer_idempotency"
        ),
    )
    buyer_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    provider: Mapped[str] = mapped_column(String(64))
    provider_reference: Mapped[str] = mapped_column(String(255))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(PaymentStatus, name="payment_status"), default=PaymentStatus.pending, index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Purchase(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "purchases"
    __table_args__ = (
        CheckConstraint("gross_amount_minor > 0", name="ck_purchase_positive_gross"),
        CheckConstraint("platform_fee_minor >= 0", name="ck_purchase_nonnegative_fee"),
        CheckConstraint("creator_amount_minor >= 0", name="ck_purchase_nonnegative_creator_amount"),
        CheckConstraint(
            "gross_amount_minor = platform_fee_minor + creator_amount_minor",
            name="ck_purchase_amounts_balance",
        ),
        CheckConstraint(
            "commission_basis_points >= 0 AND commission_basis_points <= 10000",
            name="ck_purchase_bps",
        ),
        UniqueConstraint("buyer_user_id", "content_id", name="uq_purchase_buyer_content"),
    )
    buyer_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    seller_creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    content_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_items.id", ondelete="RESTRICT"), index=True
    )
    payment_attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="RESTRICT"), unique=True
    )
    gross_amount_minor: Mapped[int] = mapped_column(Integer)
    platform_fee_minor: Mapped[int] = mapped_column(Integer)
    creator_amount_minor: Mapped[int] = mapped_column(Integer)
    commission_basis_points: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[PurchaseStatus] = mapped_column(
        Enum(PurchaseStatus, name="purchase_status"),
        default=PurchaseStatus.awaiting_payment,
        index=True,
    )
    entitlement_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("content_entitlements.id", ondelete="RESTRICT"), unique=True
    )
    ledger_transaction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), unique=True
    )
    purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PurchasePaymentAttempt(UUIDPrimaryKey, Timestamped, Base):
    """Durable attempt history for one canonical buyer/content purchase."""

    __tablename__ = "purchase_payment_attempts"
    __table_args__ = (
        CheckConstraint("attempt_number > 0", name="ck_purchase_attempt_positive_number"),
        UniqueConstraint("purchase_id", "attempt_number", name="uq_purchase_attempt_number"),
        UniqueConstraint("payment_attempt_id", name="uq_purchase_attempt_payment"),
    )
    purchase_id: Mapped[UUID] = mapped_column(
        ForeignKey("purchases.id", ondelete="RESTRICT"), index=True
    )
    payment_attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="RESTRICT"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer)


class PaymentRefundRequirement(UUIDPrimaryKey, Timestamped, Base):
    """Frozen evidence that a duplicate provider capture requires refunding."""

    __tablename__ = "payment_refund_requirements"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_payment_refund_requirement_positive_amount"),
        UniqueConstraint("payment_attempt_id", name="uq_payment_refund_requirement_attempt"),
        UniqueConstraint(
            "liability_ledger_transaction_id",
            name="uq_payment_refund_requirement_liability_ledger",
        ),
        UniqueConstraint(
            "refund_ledger_transaction_id",
            name="uq_payment_refund_requirement_refund_ledger",
        ),
        UniqueConstraint(
            "provider_refund_reference",
            name="uq_payment_refund_requirement_provider_reference",
        ),
    )
    payment_attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="RESTRICT"), index=True
    )
    source_type: Mapped[ExcessCaptureSource] = mapped_column(
        Enum(ExcessCaptureSource, name="excess_capture_source"), index=True
    )
    source_reference: Mapped[str] = mapped_column(String(64), index=True)
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[RefundRequirementStatus] = mapped_column(
        Enum(RefundRequirementStatus, name="refund_requirement_status"),
        default=RefundRequirementStatus.required,
        index=True,
    )
    reason: Mapped[str] = mapped_column(String(64), default="duplicate_capture")
    liability_ledger_transaction_id: Mapped[UUID] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT")
    )
    refund_ledger_transaction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT")
    )
    provider_refund_reference: Mapped[str | None] = mapped_column(String(255))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentWebhookEvent(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "payment_webhook_events"
    __table_args__ = (
        UniqueConstraint("provider", "external_event_id", name="uq_payment_webhook_provider_event"),
    )
    provider: Mapped[str] = mapped_column(String(64))
    external_event_id: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(64))
    payment_attempt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="SET NULL")
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StagingPaymentSandboxEvent(UUIDPrimaryKey, Timestamped, Base):
    """A durable staging-processor event, emitted only after sandbox checkout.

    This is deliberately not a financial source of truth: the signed webhook
    remains the sole path that mutates a payment attempt or settles value.
    """

    __tablename__ = "staging_payment_sandbox_events"
    __table_args__ = (
        UniqueConstraint("external_event_id", name="uq_staging_payment_sandbox_event_id"),
        UniqueConstraint("payment_attempt_id", "event_type", name="uq_staging_payment_event_type"),
    )
    payment_attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="RESTRICT"), index=True
    )
    external_event_id: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(64))
    deliver_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[SandboxEventStatus] = mapped_column(
        Enum(SandboxEventStatus, name="sandbox_event_status"),
        default=SandboxEventStatus.pending,
        index=True,
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
