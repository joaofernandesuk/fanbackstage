"""Add paid featuring inventory, immutable booking snapshots, and permissions.

Revision ID: 20260823_0025
Revises: 20260823_0024
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260823_0025"
down_revision = "20260823_0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    surface_kind = postgresql.ENUM(
        "discover_home_hero",
        "discover_creators",
        "discover_content",
        "live_now",
        "marketplace",
        "creator_search",
        "content_search",
        name="feature_surface_kind",
        create_type=False,
    )
    surface_status = postgresql.ENUM(
        "active", "paused", "disabled", name="feature_surface_status", create_type=False
    )
    target_type = postgresql.ENUM(
        "creator",
        "post",
        "video",
        "gallery",
        "marketplace_listing",
        "live_room",
        name="feature_target_type",
        create_type=False,
    )
    booking_status = postgresql.ENUM(
        "awaiting_payment",
        "scheduled",
        "active",
        "completed",
        "cancelled",
        "refunded",
        "failed",
        "suspended",
        "chargeback",
        name="feature_booking_status",
        create_type=False,
    )
    reason = postgresql.ENUM(
        "creator_ended",
        "platform_failure",
        "moderation_ineligible",
        "admin_disabled",
        name="feature_ineligibility_reason",
        create_type=False,
    )
    for enum_type in (surface_kind, surface_status, target_type, booking_status, reason):
        enum_type.create(op.get_bind(), checkfirst=True)
    op.execute("ALTER TYPE group_permission ADD VALUE IF NOT EXISTS 'manage_featuring'")
    op.execute("ALTER TYPE ledger_transaction_type ADD VALUE IF NOT EXISTS 'featuring_charge'")
    op.create_table(
        "feature_surfaces",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column("kind", surface_kind, nullable=False, unique=True),
        sa.Column("status", surface_status, nullable=False, server_default="active"),
        sa.Column(
            "cancellation_cutoff_seconds", sa.Integer(), nullable=False, server_default="3600"
        ),
    )
    op.create_index("ix_feature_surfaces_kind", "feature_surfaces", ["kind"])
    op.create_index("ix_feature_surfaces_status", "feature_surfaces", ["status"])
    op.create_table(
        "feature_slots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column(
            "surface_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("feature_surfaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("slot_key", sa.String(length=64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("surface_id", "slot_key", name="uq_feature_slot_surface_key"),
        sa.CheckConstraint("position >= 0", name="ck_feature_slot_position"),
        sa.CheckConstraint("capacity > 0", name="ck_feature_slot_capacity"),
    )
    op.create_index("ix_feature_slots_surface_id", "feature_slots", ["surface_id"])
    op.create_table(
        "feature_prices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column(
            "slot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("feature_slots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("target_type", target_type, nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint(
            "slot_id", "target_type", "duration_seconds", "version", name="uq_feature_price_version"
        ),
        sa.CheckConstraint("amount_minor > 0", name="ck_feature_price_positive"),
        sa.CheckConstraint("duration_seconds > 0", name="ck_feature_price_duration"),
    )
    op.create_index("ix_feature_prices_slot_id", "feature_prices", ["slot_id"])
    op.create_index("ix_feature_prices_target_type", "feature_prices", ["target_type"])
    op.create_table(
        "feature_bookings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column("public_id", sa.String(length=48), nullable=False, unique=True),
        sa.Column(
            "purchaser_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "owner_creator_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "surface_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("feature_surfaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "slot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("feature_slots.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("target_type", target_type, nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", booking_status, nullable=False, server_default="awaiting_payment"),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("price_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("price_version", sa.Integer(), nullable=False),
        sa.Column("cancellation_cutoff_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "payment_attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("payment_attempts.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.Column(
            "ledger_transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ledger_transactions.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.Column("reservation_expires_at", sa.DateTime(timezone=True)),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("ineligibility_reason", reason),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.UniqueConstraint(
            "purchaser_user_id", "idempotency_key", name="uq_feature_booking_idempotency"
        ),
        sa.CheckConstraint("price_minor > 0", name="ck_feature_booking_price"),
        sa.CheckConstraint("duration_seconds > 0", name="ck_feature_booking_duration"),
    )
    for column in (
        "public_id",
        "purchaser_user_id",
        "actor_user_id",
        "owner_creator_id",
        "surface_id",
        "slot_id",
        "target_type",
        "target_id",
        "status",
        "starts_at",
        "reservation_expires_at",
    ):
        op.create_index(f"ix_feature_bookings_{column}", "feature_bookings", [column])


def downgrade() -> None:
    for column in (
        "public_id",
        "purchaser_user_id",
        "actor_user_id",
        "owner_creator_id",
        "surface_id",
        "slot_id",
        "target_type",
        "target_id",
        "status",
        "starts_at",
        "reservation_expires_at",
    ):
        op.drop_index(f"ix_feature_bookings_{column}", table_name="feature_bookings")
    op.drop_table("feature_bookings")
    op.drop_index("ix_feature_prices_target_type", table_name="feature_prices")
    op.drop_index("ix_feature_prices_slot_id", table_name="feature_prices")
    op.drop_table("feature_prices")
    op.drop_index("ix_feature_slots_surface_id", table_name="feature_slots")
    op.drop_table("feature_slots")
    op.drop_index("ix_feature_surfaces_status", table_name="feature_surfaces")
    op.drop_index("ix_feature_surfaces_kind", table_name="feature_surfaces")
    op.drop_table("feature_surfaces")
    # PostgreSQL enum values are retained on downgrade so historical ledger rows remain readable.
