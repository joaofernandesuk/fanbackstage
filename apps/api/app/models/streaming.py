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
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
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


class PrivateInvitationStatus(str, enum.Enum):
    not_required = "not_required"
    pending = "pending"
    accepted = "accepted"
    declined = "declined"


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


class LiveReportStatus(str, enum.Enum):
    open = "open"
    reviewed = "reviewed"
    dismissed = "dismissed"


class LiveRecordingStatus(str, enum.Enum):
    requested = "requested"
    recording = "recording"
    completed = "completed"
    failed = "failed"


class LiveProviderControlAction(str, enum.Enum):
    delete_room = "delete_room"
    remove_participant = "remove_participant"


class LiveProviderControlStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    succeeded = "succeeded"
    failed_terminal = "failed_terminal"


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


class LiveEvent(UUIDPrimaryKey, Timestamped, Base):
    """Canonical, replay-safe public activity projection for a Live context.

    Product facts such as a ledger settlement or moderation command remain in
    their owning domain. This append-only row is the one recoverable activity
    source exposed to Live clients after reconnect, and references that fact
    rather than duplicating its authority.
    """

    __tablename__ = "live_events"
    __table_args__ = (
        CheckConstraint(
            "(live_room_id IS NOT NULL) OR (private_session_id IS NOT NULL)",
            name="ck_live_event_context",
        ),
        CheckConstraint("btrim(event_type) <> ''", name="ck_live_event_type"),
        CheckConstraint(
            "amount_minor IS NULL OR amount_minor > 0", name="ck_live_event_positive_amount"
        ),
        UniqueConstraint("idempotency_key", name="uq_live_events_idempotency"),
        Index("ix_live_events_room_occurred", "live_room_id", "occurred_at", "id"),
        Index("ix_live_events_session_occurred", "private_session_id", "occurred_at", "id"),
    )
    live_room_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("live_rooms.id", ondelete="RESTRICT"), index=True
    )
    private_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("private_sessions.id", ondelete="RESTRICT"), index=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    ledger_transaction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), unique=True
    )
    source_type: Mapped[str | None] = mapped_column(String(80))
    source_id: Mapped[str | None] = mapped_column(String(255))
    amount_minor: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str | None] = mapped_column(String(3))
    presentation_hidden: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class LiveCommerceKind(str, enum.Enum):
    tip = "tip"
    gift = "gift"
    paid_request = "paid_request"


class LiveCommerceStatus(str, enum.Enum):
    pending_payment = "pending_payment"
    paid_pending_creator = "paid_pending_creator"
    accepted = "accepted"
    declined = "declined"
    completed = "completed"
    refunded = "refunded"
    expired = "expired"
    disputed = "disputed"


class LiveReactionType(str, enum.Enum):
    love = "love"
    fire = "fire"
    applause = "applause"
    wow = "wow"


class LiveGiftCatalogItem(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "live_gift_catalog_items"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_live_gift_amount_positive"),
        CheckConstraint("btrim(name) <> ''", name="ck_live_gift_name"),
    )
    name: Mapped[str] = mapped_column(String(80), unique=True)
    icon: Mapped[str] = mapped_column(String(120))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    category: Mapped[str | None] = mapped_column(String(48))


class LiveTipMenuItem(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "live_tip_menu_items"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_live_tip_menu_amount_positive"),
        CheckConstraint("btrim(label) <> ''", name="ck_live_tip_menu_label"),
        UniqueConstraint("creator_id", "sort_order", name="uq_live_tip_menu_creator_order"),
    )
    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(100))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class LivePaidRequestOption(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "live_paid_request_options"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_live_paid_request_option_amount_positive"),
        CheckConstraint("btrim(label) <> ''", name="ck_live_paid_request_option_label"),
    )
    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(100))
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    requires_creator_acceptance: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )


class LiveGoal(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "live_goals"
    __table_args__ = (
        CheckConstraint("target_amount_minor > 0", name="ck_live_goal_target_positive"),
        CheckConstraint("btrim(title) <> ''", name="ck_live_goal_title"),
    )
    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(140))
    target_amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LiveCommerceCharge(UUIDPrimaryKey, Timestamped, Base):
    """One payment-backed Live action with frozen settlement inputs."""

    __tablename__ = "live_commerce_charges"
    __table_args__ = (
        CheckConstraint("gross_amount_minor > 0", name="ck_live_charge_gross_positive"),
        CheckConstraint("commission_basis_points >= 0", name="ck_live_charge_commission"),
        Index("ix_live_charge_room_status", "live_room_id", "status", "created_at"),
    )
    live_room_id: Mapped[UUID] = mapped_column(
        ForeignKey("live_rooms.id", ondelete="RESTRICT"), index=True
    )
    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    buyer_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    kind: Mapped[LiveCommerceKind] = mapped_column(
        Enum(LiveCommerceKind, name="live_commerce_kind"), index=True
    )
    status: Mapped[LiveCommerceStatus] = mapped_column(
        Enum(LiveCommerceStatus, name="live_commerce_status"), index=True
    )
    gift_catalog_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("live_gift_catalog_items.id", ondelete="RESTRICT")
    )
    tip_menu_item_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("live_tip_menu_items.id", ondelete="RESTRICT")
    )
    paid_request_option_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("live_paid_request_options.id", ondelete="RESTRICT")
    )
    request_label: Mapped[str | None] = mapped_column(String(100))
    request_message: Mapped[str | None] = mapped_column(String(500))
    gross_amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    commission_basis_points: Mapped[int] = mapped_column(Integer)
    payment_attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="RESTRICT"), unique=True
    )
    ledger_transaction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), unique=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    creator_acceptance_required: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )


class LiveReactionAggregate(UUIDPrimaryKey, Timestamped, Base):
    """Bounded per-room counters; individual reaction bursts are deliberately ephemeral."""

    __tablename__ = "live_reaction_aggregates"
    __table_args__ = (
        CheckConstraint(
            "reaction_count >= 0", name="ck_live_reaction_aggregate_nonnegative"
        ),
        UniqueConstraint(
            "live_room_id",
            "reaction_type",
            name="uq_live_reaction_aggregate_room_type",
        ),
    )
    live_room_id: Mapped[UUID] = mapped_column(
        ForeignKey("live_rooms.id", ondelete="CASCADE"), index=True
    )
    reaction_type: Mapped[LiveReactionType] = mapped_column(
        Enum(LiveReactionType, name="live_reaction_type")
    )
    reaction_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class LiveBan(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "live_bans"
    __table_args__ = (UniqueConstraint("live_room_id", "user_id", name="uq_live_room_ban"),)
    live_room_id: Mapped[UUID] = mapped_column(
        ForeignKey("live_rooms.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    actor_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reason: Mapped[str] = mapped_column(String(500))


class LiveReport(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "live_reports"
    __table_args__ = (
        Index("ix_live_reports_status_created", "status", "created_at", "id"),
        UniqueConstraint(
            "reporter_user_id",
            "live_room_id",
            "live_chat_message_id",
            "reason",
            name="uq_live_report_reporter_target_reason",
        ),
    )
    reporter_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    live_room_id: Mapped[UUID] = mapped_column(
        ForeignKey("live_rooms.id", ondelete="RESTRICT"), index=True
    )
    live_chat_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("live_chat_messages.id", ondelete="SET NULL"), index=True
    )
    reason: Mapped[str] = mapped_column(String(120))
    details: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[LiveReportStatus] = mapped_column(
        Enum(LiveReportStatus, name="live_report_status"),
        default=LiveReportStatus.open,
        nullable=False,
        index=True,
    )


class LiveRecording(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "live_recordings"
    live_room_id: Mapped[UUID] = mapped_column(
        ForeignKey("live_rooms.id", ondelete="RESTRICT"), unique=True, index=True
    )
    status: Mapped[LiveRecordingStatus] = mapped_column(
        Enum(LiveRecordingStatus, name="live_recording_status"),
        default=LiveRecordingStatus.requested,
        nullable=False,
        index=True,
    )
    provider_egress_id: Mapped[str | None] = mapped_column(String(255), unique=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    invited_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    invitation_status: Mapped[PrivateInvitationStatus] = mapped_column(
        Enum(PrivateInvitationStatus, name="private_invitation_status"),
        default=PrivateInvitationStatus.not_required,
        nullable=False,
        index=True,
    )
    invitation_responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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


class LiveProviderControlIntent(UUIDPrimaryKey, Timestamped, Base):
    """Durable, replay-safe command for the LiveKit server-control boundary."""

    __tablename__ = "live_provider_control_intents"
    __table_args__ = (
        CheckConstraint(
            "(action = 'delete_room' AND participant_identity IS NULL) OR "
            "(action = 'remove_participant' AND "
            "participant_identity IS NOT NULL AND btrim(participant_identity) <> '')",
            name="ck_live_provider_control_action_target",
        ),
        CheckConstraint(
            "btrim(target_type) <> '' AND btrim(target_id) <> '' "
            "AND btrim(provider_room_name) <> '' AND btrim(reason) <> '' "
            "AND btrim(idempotency_key) <> ''",
            name="ck_live_provider_control_required_text",
        ),
        CheckConstraint(
            "attempt_count >= 0",
            name="ck_live_provider_control_attempt_count",
        ),
        CheckConstraint(
            "(last_error_code IS NULL AND last_error_at IS NULL) OR "
            "(last_error_code IS NOT NULL AND last_error_at IS NOT NULL)",
            name="ck_live_provider_control_error_pair",
        ),
        CheckConstraint(
            "(status = 'pending' AND retryable IS TRUE "
            "AND next_attempt_at IS NOT NULL AND lease_expires_at IS NULL "
            "AND succeeded_at IS NULL AND terminal_failed_at IS NULL) OR "
            "(status = 'processing' AND retryable IS TRUE "
            "AND next_attempt_at IS NULL AND lease_expires_at IS NOT NULL "
            "AND last_attempt_at IS NOT NULL AND attempt_count > 0 "
            "AND succeeded_at IS NULL AND terminal_failed_at IS NULL) OR "
            "(status = 'succeeded' AND retryable IS FALSE "
            "AND next_attempt_at IS NULL AND lease_expires_at IS NULL "
            "AND succeeded_at IS NOT NULL AND terminal_failed_at IS NULL) OR "
            "(status = 'failed_terminal' AND retryable IS FALSE "
            "AND next_attempt_at IS NULL AND lease_expires_at IS NULL "
            "AND succeeded_at IS NULL AND terminal_failed_at IS NOT NULL "
            "AND last_error_code IS NOT NULL AND last_error_at IS NOT NULL)",
            name="ck_live_provider_control_status_state",
        ),
        Index(
            "ix_live_provider_control_due",
            "status",
            "next_attempt_at",
            "lease_expires_at",
            "created_at",
            "id",
        ),
    )
    action: Mapped[LiveProviderControlAction] = mapped_column(
        Enum(LiveProviderControlAction, name="live_provider_control_action"),
        nullable=False,
    )
    target_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_room_name: Mapped[str] = mapped_column(String(128), nullable=False)
    participant_identity: Mapped[str | None] = mapped_column(String(255))
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    actor_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    status: Mapped[LiveProviderControlStatus] = mapped_column(
        Enum(LiveProviderControlStatus, name="live_provider_control_status"),
        default=LiveProviderControlStatus.pending,
        nullable=False,
        index=True,
    )
    retryable: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    terminal_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(96))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
