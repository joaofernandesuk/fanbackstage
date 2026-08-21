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
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamped, UUIDPrimaryKey


class MessagingPermission(str, enum.Enum):
    anyone = "anyone"
    followers = "followers"
    subscribers = "subscribers"
    previous_customers = "previous_customers"
    nobody = "nobody"


class MessageType(str, enum.Enum):
    text = "text"
    media = "media"
    content_reference = "content_reference"
    system = "system"


class MessageStatus(str, enum.Enum):
    sent = "sent"
    removed = "removed"


class CampaignStatus(str, enum.Enum):
    draft = "draft"
    scheduled = "scheduled"
    processing = "processing"
    completed = "completed"
    cancelled = "cancelled"


class AudienceSegment(str, enum.Enum):
    followers = "followers"
    active_subscribers = "active_subscribers"
    expired_subscribers = "expired_subscribers"
    previous_customers = "previous_customers"


class Conversation(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "conversations"
    __table_args__ = (
        UniqueConstraint("creator_id", "viewer_user_id", name="uq_conversation_creator_viewer"),
        Index("ix_conversation_creator_last_message", "creator_id", "last_message_at"),
    )
    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    viewer_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    archived_by_creator: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    archived_by_viewer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    muted_by_creator: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    muted_by_viewer: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ConversationParticipant(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "conversation_participants"
    __table_args__ = (
        UniqueConstraint("conversation_id", "user_id", name="uq_conversation_participant"),
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MessagingSettings(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "creator_messaging_settings"
    __table_args__ = (
        CheckConstraint(
            "send_fee_minor IS NULL OR send_fee_minor > 0", name="ck_messaging_send_fee_positive"
        ),
    )
    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="CASCADE"), unique=True
    )
    permission: Mapped[MessagingPermission] = mapped_column(
        Enum(MessagingPermission, name="messaging_permission"), default=MessagingPermission.anyone
    )
    send_fee_minor: Mapped[int | None] = mapped_column(Integer)
    send_fee_currency: Mapped[str | None] = mapped_column(String(3))
    subscribers_free: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Message(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_message_conversation_created", "conversation_id", "created_at", "id"),
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("conversations.id", ondelete="RESTRICT"), index=True
    )
    sender_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    reply_to_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="RESTRICT")
    )
    message_type: Mapped[MessageType] = mapped_column(
        Enum(MessageType, name="message_type"), default=MessageType.text
    )
    body: Mapped[str | None] = mapped_column(Text)
    status: Mapped[MessageStatus] = mapped_column(
        Enum(MessageStatus, name="message_status"), default=MessageStatus.sent, index=True
    )
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MessageAttachment(UUIDPrimaryKey, Timestamped, Base):
    """A message-owned offer around an existing, private media asset.

    The asset is never copied and this row deliberately stores commercial terms
    so they cannot be changed after a recipient has purchased it.
    """

    __tablename__ = "message_attachments"
    __table_args__ = (
        CheckConstraint(
            "unlock_price_minor IS NULL OR unlock_price_minor > 0",
            name="ck_message_attachment_price",
        ),
        CheckConstraint(
            "(unlock_price_minor IS NULL AND unlock_currency IS NULL) OR (unlock_price_minor IS NOT NULL AND unlock_currency IS NOT NULL)",
            name="ck_message_attachment_currency",
        ),
        UniqueConstraint("message_id", "media_asset_id", name="uq_message_attachment_asset"),
    )
    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("messages.id", ondelete="RESTRICT"), index=True
    )
    media_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("media_assets.id", ondelete="RESTRICT"), index=True
    )
    unlock_price_minor: Mapped[int | None] = mapped_column(Integer)
    unlock_currency: Mapped[str | None] = mapped_column(String(3))


class MessageUnlockPurchase(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "message_unlock_purchases"
    __table_args__ = (
        UniqueConstraint(
            "buyer_user_id", "message_attachment_id", name="uq_message_unlock_buyer_attachment"
        ),
        UniqueConstraint("payment_attempt_id", name="uq_message_unlock_payment_attempt"),
        UniqueConstraint("ledger_transaction_id", name="uq_message_unlock_ledger"),
        CheckConstraint(
            "gross_amount_minor = platform_fee_minor + creator_amount_minor",
            name="ck_message_unlock_balance",
        ),
    )
    buyer_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    seller_creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    message_attachment_id: Mapped[UUID] = mapped_column(
        ForeignKey("message_attachments.id", ondelete="RESTRICT"), index=True
    )
    payment_attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="RESTRICT")
    )
    gross_amount_minor: Mapped[int] = mapped_column(Integer)
    platform_fee_minor: Mapped[int] = mapped_column(Integer)
    creator_amount_minor: Mapped[int] = mapped_column(Integer)
    commission_basis_points: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(32), default="awaiting_payment", index=True)
    ledger_transaction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT")
    )
    purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PendingMessageSend(UUIDPrimaryKey, Timestamped, Base):
    """A paid send is persisted only after provider-verified settlement."""

    __tablename__ = "pending_message_sends"
    __table_args__ = (
        UniqueConstraint("payment_attempt_id", name="uq_pending_message_send_payment"),
        UniqueConstraint("message_id", name="uq_pending_message_send_message"),
        CheckConstraint(
            "gross_amount_minor = platform_fee_minor + creator_amount_minor",
            name="ck_pending_message_send_balance",
        ),
    )
    buyer_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    body: Mapped[str] = mapped_column(Text)
    payment_attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="RESTRICT")
    )
    gross_amount_minor: Mapped[int] = mapped_column(Integer)
    platform_fee_minor: Mapped[int] = mapped_column(Integer)
    creator_amount_minor: Mapped[int] = mapped_column(Integer)
    commission_basis_points: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(32), default="awaiting_payment", index=True)
    ledger_transaction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), unique=True
    )
    message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="RESTRICT"), unique=True
    )


class MassMessageCampaign(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "mass_message_campaigns"
    __table_args__ = (Index("ix_mass_campaign_due", "status", "scheduled_at"),)
    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    audience_segment: Mapped[AudienceSegment] = mapped_column(
        Enum(AudienceSegment, name="message_audience_segment")
    )
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus, name="mass_message_campaign_status"),
        default=CampaignStatus.draft,
        index=True,
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MassMessageRecipient(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "mass_message_recipients"
    __table_args__ = (
        UniqueConstraint("campaign_id", "recipient_user_id", name="uq_mass_campaign_recipient"),
    )
    campaign_id: Mapped[UUID] = mapped_column(
        ForeignKey("mass_message_campaigns.id", ondelete="RESTRICT"), index=True
    )
    recipient_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="RESTRICT"), unique=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserBlock(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "user_blocks"
    __table_args__ = (
        UniqueConstraint("blocker_user_id", "blocked_user_id", name="uq_user_block"),
        CheckConstraint("blocker_user_id <> blocked_user_id", name="ck_user_block_not_self"),
    )
    blocker_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    blocked_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
