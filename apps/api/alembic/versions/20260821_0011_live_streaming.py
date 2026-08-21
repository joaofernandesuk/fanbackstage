"""Add Phase 7 live-room and private-session foundation."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260821_0011"
down_revision = "20260821_0010"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    for name, values in {
        "live_room_status": "scheduled, starting, live, ending, ended, failed, suspended",
        "live_access_mode": "public, followers, subscribers",
        "live_participant_role": "creator, viewer, moderator, cohost",
        "private_session_mode": "one_to_one, two_to_one",
        "private_request_status": "pending, accepted, rejected, expired, cancelled",
        "private_session_status": "awaiting_payment_authorization, ready, connecting, active, reconnecting, ending, ended, settled, failed, cancelled, disputed",
        "session_participant_role": "creator, payer, invited_viewer",
        "live_chat_kind": "text, system",
    }.items():
        quoted = ", ".join(f"'{value.strip()}'" for value in values.split(","))
        op.execute(f"CREATE TYPE {name} AS ENUM ({quoted})")
    op.execute("ALTER TYPE ledger_transaction_type ADD VALUE IF NOT EXISTS 'private_live_session'")

    op.create_table(
        "creator_live_settings",
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creator_profiles.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "private_sessions_enabled", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column("one_to_one_price_minor", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("two_to_one_price_minor", sa.Integer(), nullable=False, server_default="150"),
        sa.Column("currency", sa.String(3), nullable=False, server_default="EUR"),
        sa.Column("minimum_minutes", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("max_authorization_minor", sa.Integer(), nullable=False, server_default="6000"),
        *_timestamps(),
        sa.CheckConstraint("one_to_one_price_minor > 0", name="ck_live_settings_one_to_one_price"),
        sa.CheckConstraint("two_to_one_price_minor > 0", name="ck_live_settings_two_to_one_price"),
        sa.CheckConstraint("minimum_minutes > 0", name="ck_live_settings_minimum_minutes"),
    )
    op.create_table(
        "live_rooms",
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("public_id", sa.String(48), nullable=False, unique=True),
        sa.Column("provider_room_name", sa.String(128), nullable=False, unique=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="live_room_status", create_type=False),
            nullable=False,
            server_default="scheduled",
        ),
        sa.Column(
            "access_mode",
            postgresql.ENUM(name="live_access_mode", create_type=False),
            nullable=False,
            server_default="public",
        ),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("viewer_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("peak_viewer_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    op.create_index("ix_live_room_discovery", "live_rooms", ["status", "started_at", "id"])
    op.create_index("ix_live_room_creator_status", "live_rooms", ["creator_id", "status"])
    op.create_table(
        "live_participants",
        sa.Column(
            "live_room_id",
            sa.Uuid(),
            sa.ForeignKey("live_rooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "role", postgresql.ENUM(name="live_participant_role", create_type=False), nullable=False
        ),
        sa.Column("joined_at", sa.DateTime(timezone=True)),
        sa.Column("left_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("live_room_id", "user_id", name="uq_live_room_participant"),
    )
    op.create_table(
        "live_chat_messages",
        sa.Column(
            "live_room_id",
            sa.Uuid(),
            sa.ForeignKey("live_rooms.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sender_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "kind",
            postgresql.ENUM(name="live_chat_kind", create_type=False),
            nullable=False,
            server_default="text",
        ),
        sa.Column("body", sa.Text(), nullable=False),
        *_timestamps(),
    )
    op.create_index(
        "ix_live_chat_room_created", "live_chat_messages", ["live_room_id", "created_at", "id"]
    )
    op.create_table(
        "live_bans",
        sa.Column(
            "live_room_id",
            sa.Uuid(),
            sa.ForeignKey("live_rooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "actor_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(500), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("live_room_id", "user_id", name="uq_live_room_ban"),
    )
    op.create_table(
        "private_session_requests",
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "requester_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("invited_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column(
            "mode", postgresql.ENUM(name="private_session_mode", create_type=False), nullable=False
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="private_request_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("per_minute_price_minor", sa.Integer(), nullable=False),
        sa.Column("minimum_minutes", sa.Integer(), nullable=False),
        sa.Column("minimum_charge_minor", sa.Integer(), nullable=False),
        sa.Column("max_authorization_minor", sa.Integer(), nullable=False),
        sa.Column("commission_basis_points", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("note", sa.String(500)),
        *_timestamps(),
    )
    op.create_index(
        "ix_private_request_creator_status_created",
        "private_session_requests",
        ["creator_id", "status", "created_at"],
    )
    op.create_table(
        "private_sessions",
        sa.Column(
            "request_id",
            sa.Uuid(),
            sa.ForeignKey("private_session_requests.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "payer_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "mode", postgresql.ENUM(name="private_session_mode", create_type=False), nullable=False
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="private_session_status", create_type=False),
            nullable=False,
            server_default="awaiting_payment_authorization",
        ),
        sa.Column("provider_room_name", sa.String(128), nullable=False, unique=True),
        sa.Column(
            "payment_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("payment_attempts.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.Column("per_minute_price_minor", sa.Integer(), nullable=False),
        sa.Column("minimum_minutes", sa.Integer(), nullable=False),
        sa.Column("minimum_charge_minor", sa.Integer(), nullable=False),
        sa.Column("max_authorization_minor", sa.Integer(), nullable=False),
        sa.Column("commission_basis_points", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ready_at", sa.DateTime(timezone=True)),
        sa.Column("active_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("disconnected_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("ended_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("end_reason", sa.String(80)),
        sa.Column("billable_seconds", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
    )
    op.create_index(
        "ix_private_session_creator_status", "private_sessions", ["creator_id", "status"]
    )
    op.create_table(
        "private_session_participants",
        sa.Column(
            "private_session_id",
            sa.Uuid(),
            sa.ForeignKey("private_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "role",
            postgresql.ENUM(name="session_participant_role", create_type=False),
            nullable=False,
        ),
        sa.Column("joined_at", sa.DateTime(timezone=True)),
        sa.Column("left_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("private_session_id", "user_id", name="uq_private_session_participant"),
    )
    op.create_table(
        "private_session_settlements",
        sa.Column(
            "private_session_id",
            sa.Uuid(),
            sa.ForeignKey("private_sessions.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("gross_amount_minor", sa.Integer(), nullable=False),
        sa.Column("platform_fee_minor", sa.Integer(), nullable=False),
        sa.Column("creator_amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("billable_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "ledger_transaction_id",
            sa.Uuid(),
            sa.ForeignKey("ledger_transactions.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        *_timestamps(),
    )
    op.create_table(
        "provider_live_events",
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("external_event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("live_room_id", sa.Uuid(), sa.ForeignKey("live_rooms.id", ondelete="SET NULL")),
        sa.Column(
            "private_session_id",
            sa.Uuid(),
            sa.ForeignKey("private_sessions.id", ondelete="SET NULL"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("provider", "external_event_id", name="uq_provider_live_event"),
    )


def downgrade() -> None:
    for table in [
        "provider_live_events",
        "private_session_settlements",
        "private_session_participants",
        "private_sessions",
        "private_session_requests",
        "live_bans",
        "live_chat_messages",
        "live_participants",
        "live_rooms",
        "creator_live_settings",
    ]:
        op.drop_table(table)
    for name in [
        "live_chat_kind",
        "session_participant_role",
        "private_session_status",
        "private_request_status",
        "private_session_mode",
        "live_participant_role",
        "live_access_mode",
        "live_room_status",
    ]:
        op.execute(f"DROP TYPE IF EXISTS {name}")
