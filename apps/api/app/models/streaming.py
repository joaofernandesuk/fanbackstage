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


class LiveRoomStatus(str, enum.Enum):
    scheduled = "scheduled"
    starting = "starting"
    live = "live"
    ending = "ending"
    ended = "ended"
    failed = "failed"
    suspended = "suspended"


class LiveAccessMode(str, enum.Enum):
    public = "public"
    followers = "followers"
    subscribers = "subscribers"


class LiveParticipantRole(str, enum.Enum):
    creator = "creator"
    viewer = "viewer"
    moderator = "moderator"
    cohost = "cohost"


class PrivateSessionMode(str, enum.Enum):
    one_to_one = "one_to_one"
    two_to_one = "two_to_one"


class PrivateRequestStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    expired = "expired"
    cancelled = "cancelled"


class PrivateSessionStatus(str, enum.Enum):
    awaiting_payment_authorization = "awaiting_payment_authorization"
    ready = "ready"
    connecting = "connecting"
    active = "active"
    reconnecting = "reconnecting"
    ending = "ending"
    ended = "ended"
    settled = "settled"
    failed = "failed"
    cancelled = "cancelled"
    disputed = "disputed"


class SessionParticipantRole(str, enum.Enum):
    creator = "creator"
    payer = "payer"
    invited_viewer = "invited_viewer"


class LiveChatKind(str, enum.Enum):
    text = "text"
    system = "system"


class LiveRoom(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "live_rooms"
    __table_args__ = (
        Index("ix_live_room_discovery", "status", "started_at", "id"),
        Index("ix_live_room_creator_status", "creator_id", "status"),
    )
    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    public_id: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    provider_room_name: Mapped[str] = mapped_column(String(128), unique=True)
    status: Mapped[LiveRoomStatus] = mapped_column(
        Enum(LiveRoomStatus, name="live_room_status"), default=LiveRoomStatus.scheduled, index=True
    )
    access_mode: Mapped[LiveAccessMode] = mapped_column(
        Enum(LiveAccessMode, name="live_access_mode"), default=LiveAccessMode.public
    )
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    viewer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    peak_viewer_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LiveParticipant(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "live_participants"
    __table_args__ = (UniqueConstraint("live_room_id", "user_id", name="uq_live_room_participant"),)
    live_room_id: Mapped[UUID] = mapped_column(
        ForeignKey("live_rooms.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    role: Mapped[LiveParticipantRole] = mapped_column(
        Enum(LiveParticipantRole, name="live_participant_role")
    )
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LiveChatMessage(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "live_chat_messages"
    __table_args__ = (Index("ix_live_chat_room_created", "live_room_id", "created_at", "id"),)
    live_room_id: Mapped[UUID] = mapped_column(
        ForeignKey("live_rooms.id", ondelete="RESTRICT"), index=True
    )
    sender_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    kind: Mapped[LiveChatKind] = mapped_column(
        Enum(LiveChatKind, name="live_chat_kind"), default=LiveChatKind.text
    )
    body: Mapped[str] = mapped_column(Text)


class LiveBan(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "live_bans"
    __table_args__ = (UniqueConstraint("live_room_id", "user_id", name="uq_live_room_ban"),)
    live_room_id: Mapped[UUID] = mapped_column(
        ForeignKey("live_rooms.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reason: Mapped[str] = mapped_column(String(500))


class CreatorLiveSettings(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "creator_live_settings"
    __table_args__ = (
        CheckConstraint("one_to_one_price_minor > 0", name="ck_live_settings_one_to_one_price"),
        CheckConstraint("two_to_one_price_minor > 0", name="ck_live_settings_two_to_one_price"),
        CheckConstraint("minimum_minutes > 0", name="ck_live_settings_minimum_minutes"),
    )
    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="CASCADE"), unique=True
    )
    private_sessions_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    one_to_one_price_minor: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    two_to_one_price_minor: Mapped[int] = mapped_column(Integer, default=150, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="EUR", nullable=False)
    minimum_minutes: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    max_authorization_minor: Mapped[int] = mapped_column(Integer, default=6000, nullable=False)


class PrivateSessionRequest(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "private_session_requests"
    __table_args__ = (
        Index("ix_private_request_creator_status_created", "creator_id", "status", "created_at"),
    )
    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    requester_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    mode: Mapped[PrivateSessionMode] = mapped_column(
        Enum(PrivateSessionMode, name="private_session_mode")
    )
    status: Mapped[PrivateRequestStatus] = mapped_column(
        Enum(PrivateRequestStatus, name="private_request_status"),
        default=PrivateRequestStatus.pending,
        index=True,
    )
    per_minute_price_minor: Mapped[int] = mapped_column(Integer)
    minimum_minutes: Mapped[int] = mapped_column(Integer)
    minimum_charge_minor: Mapped[int] = mapped_column(Integer)
    max_authorization_minor: Mapped[int] = mapped_column(Integer)
    commission_basis_points: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(500))


class PrivateSession(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "private_sessions"
    __table_args__ = (Index("ix_private_session_creator_status", "creator_id", "status"),)
    request_id: Mapped[UUID] = mapped_column(
        ForeignKey("private_session_requests.id", ondelete="RESTRICT"), unique=True
    )
    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    payer_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    mode: Mapped[PrivateSessionMode] = mapped_column(
        Enum(PrivateSessionMode, name="private_session_mode", create_type=False)
    )
    status: Mapped[PrivateSessionStatus] = mapped_column(
        Enum(PrivateSessionStatus, name="private_session_status"),
        default=PrivateSessionStatus.awaiting_payment_authorization,
        index=True,
    )
    provider_room_name: Mapped[str] = mapped_column(String(128), unique=True)
    payment_attempt_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="RESTRICT"), unique=True
    )
    per_minute_price_minor: Mapped[int] = mapped_column(Integer)
    minimum_minutes: Mapped[int] = mapped_column(Integer)
    minimum_charge_minor: Mapped[int] = mapped_column(Integer)
    max_authorization_minor: Mapped[int] = mapped_column(Integer)
    commission_basis_points: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    end_reason: Mapped[str | None] = mapped_column(String(80))
    billable_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class SessionParticipant(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "private_session_participants"
    __table_args__ = (
        UniqueConstraint("private_session_id", "user_id", name="uq_private_session_participant"),
    )
    private_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("private_sessions.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    role: Mapped[SessionParticipantRole] = mapped_column(
        Enum(SessionParticipantRole, name="session_participant_role")
    )
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PrivateSessionSettlement(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "private_session_settlements"
    private_session_id: Mapped[UUID] = mapped_column(
        ForeignKey("private_sessions.id", ondelete="RESTRICT"), unique=True
    )
    gross_amount_minor: Mapped[int] = mapped_column(Integer)
    platform_fee_minor: Mapped[int] = mapped_column(Integer)
    creator_amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    billable_seconds: Mapped[int] = mapped_column(Integer)
    ledger_transaction_id: Mapped[UUID] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), unique=True
    )


class ProviderLiveEvent(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "provider_live_events"
    __table_args__ = (
        UniqueConstraint("provider", "external_event_id", name="uq_provider_live_event"),
    )
    provider: Mapped[str] = mapped_column(String(64))
    external_event_id: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(64))
    live_room_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("live_rooms.id", ondelete="SET NULL")
    )
    private_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("private_sessions.id", ondelete="SET NULL")
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
