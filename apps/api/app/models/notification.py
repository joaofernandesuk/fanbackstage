import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamped, UUIDPrimaryKey


class NotificationClass(str, enum.Enum):
    transactional = "transactional"
    marketing = "marketing"


class NotificationChannel(str, enum.Enum):
    email = "email"
    in_app = "in_app"


class NotificationPriority(str, enum.Enum):
    critical_security = "critical_security"
    transactional = "transactional"
    normal = "normal"
    marketing = "marketing"


class DeliveryStatus(str, enum.Enum):
    queued = "queued"
    processing = "processing"
    sent = "sent"
    delivered = "delivered"
    failed_retryable = "failed_retryable"
    failed_permanent = "failed_permanent"
    suppressed = "suppressed"


class SuppressionReason(str, enum.Enum):
    hard_bounce = "hard_bounce"
    complaint = "complaint"
    manual = "manual"
    marketing_unsubscribe = "marketing_unsubscribe"


class NotificationIntent(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "notification_intents"
    __table_args__ = (UniqueConstraint("idempotency_key"),)
    recipient_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    notification_type: Mapped[str] = mapped_column(String(96), index=True)
    classification: Mapped[NotificationClass] = mapped_column(
        Enum(NotificationClass, name="notification_class")
    )
    priority: Mapped[NotificationPriority] = mapped_column(
        Enum(NotificationPriority, name="notification_priority")
    )
    source_domain: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[str] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(192))
    payload_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    secure_payload: Mapped[str | None] = mapped_column(Text)


class InAppNotification(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "in_app_notifications"
    __table_args__ = (UniqueConstraint("intent_id"),)
    intent_id: Mapped[UUID] = mapped_column(
        ForeignKey("notification_intents.id", ondelete="CASCADE")
    )
    recipient_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    notification_type: Mapped[str] = mapped_column(String(96), index=True)
    title: Mapped[str] = mapped_column(String(180))
    body: Mapped[str] = mapped_column(String(500))
    target_path: Mapped[str | None] = mapped_column(String(512))
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class NotificationPreference(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "notification_preferences"
    __table_args__ = (UniqueConstraint("user_id", "category"),)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(48))
    email_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    in_app_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    consented_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_source: Mapped[str | None] = mapped_column(String(96))


class EmailSuppression(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "email_suppressions"
    __table_args__ = (UniqueConstraint("email_hash", "reason"),)
    email_hash: Mapped[str] = mapped_column(String(64), index=True)
    reason: Mapped[SuppressionReason] = mapped_column(
        Enum(SuppressionReason, name="suppression_reason")
    )
    marketing_only: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str] = mapped_column(String(96))


class NotificationDeliveryAttempt(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "notification_delivery_attempts"
    intent_id: Mapped[UUID] = mapped_column(
        ForeignKey("notification_intents.id", ondelete="CASCADE"), index=True
    )
    channel: Mapped[NotificationChannel] = mapped_column(
        Enum(NotificationChannel, name="notification_channel")
    )
    status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, name="delivery_status"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(64))
    provider_message_id: Mapped[str | None] = mapped_column(String(255), unique=True, index=True)
    recipient_snapshot: Mapped[str | None] = mapped_column(String(320))
    template_key: Mapped[str | None] = mapped_column(String(96))
    template_version: Mapped[int | None] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(96))
