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
    refund_clearing = "refund_clearing"


class LedgerDirection(str, enum.Enum):
    debit = "debit"
    credit = "credit"


class LedgerTransactionType(str, enum.Enum):
    ppv_purchase = "ppv_purchase"
    earnings_release = "earnings_release"
    refund = "refund"
    chargeback = "chargeback"


class PaymentStatus(str, enum.Enum):
    pending = "pending"
    succeeded = "succeeded"
    failed = "failed"
    refunded = "refunded"
    disputed = "disputed"
    chargeback = "chargeback"


class PurchaseStatus(str, enum.Enum):
    awaiting_payment = "awaiting_payment"
    paid = "paid"
    failed = "failed"
    refunded = "refunded"
    disputed = "disputed"
    chargeback = "chargeback"


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
            postgresql_where="owner_creator_id IS NULL",
        ),
    )
    owner_creator_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    kind: Mapped[LedgerAccountKind] = mapped_column(
        Enum(LedgerAccountKind, name="ledger_account_kind"), index=True
    )
    currency: Mapped[str] = mapped_column(String(3))


class LedgerTransaction(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "ledger_transactions"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_ledger_transactions_idempotency"),
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
