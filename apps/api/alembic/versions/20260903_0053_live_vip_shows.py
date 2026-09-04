"""Add paid group VIP shows to public Live rooms.

Revision ID: 20260903_0053
Revises: 20260903_0052
Create Date: 2026-09-03

The financial enum labels are intentionally forward-only because PostgreSQL
cannot safely remove values that may be referenced by immutable history.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260903_0053"
down_revision = "20260903_0052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE live_commerce_kind ADD VALUE IF NOT EXISTS 'vip_admission'")
    op.execute(
        "ALTER TYPE ledger_transaction_type ADD VALUE IF NOT EXISTS 'live_vip_admission'"
    )
    op.execute(
        "ALTER TYPE excess_capture_source ADD VALUE IF NOT EXISTS 'live_vip_admission'"
    )
    vip_status = postgresql.ENUM(
        "preshow",
        "awaiting_creator",
        "active",
        "completed",
        "cancelled",
        name="live_vip_show_status",
        create_type=False,
    )
    vip_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "live_vip_shows",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "live_room_id",
            sa.UUID(),
            sa.ForeignKey("live_rooms.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "creator_id",
            sa.UUID(),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", vip_status, nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("goal_amount_minor", sa.Integer(), nullable=False),
        sa.Column("buy_in_amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("preshow_ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_seconds", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("ends_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("goal_amount_minor > 0", name="ck_live_vip_goal_positive"),
        sa.CheckConstraint("buy_in_amount_minor > 0", name="ck_live_vip_buy_in_positive"),
        sa.CheckConstraint(
            "duration_seconds BETWEEN 300 AND 900", name="ck_live_vip_duration"
        ),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_live_vip_title"),
    )
    op.create_index("ix_live_vip_shows_live_room_id", "live_vip_shows", ["live_room_id"])
    op.create_index("ix_live_vip_shows_creator_id", "live_vip_shows", ["creator_id"])
    op.create_index("ix_live_vip_shows_status", "live_vip_shows", ["status"])
    op.create_index(
        "ix_live_vip_status_preshow", "live_vip_shows", ["status", "preshow_ends_at"]
    )
    op.create_index("ix_live_vip_shows_preshow_ends_at", "live_vip_shows", ["preshow_ends_at"])
    op.create_index("ix_live_vip_shows_ends_at", "live_vip_shows", ["ends_at"])
    op.add_column("live_commerce_charges", sa.Column("vip_show_id", sa.UUID()))
    op.create_foreign_key(
        "fk_live_commerce_charge_vip_show",
        "live_commerce_charges",
        "live_vip_shows",
        ["vip_show_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_live_commerce_charges_vip_show_id",
        "live_commerce_charges",
        ["vip_show_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_live_commerce_charges_vip_show_id", table_name="live_commerce_charges")
    op.drop_constraint(
        "fk_live_commerce_charge_vip_show", "live_commerce_charges", type_="foreignkey"
    )
    op.drop_column("live_commerce_charges", "vip_show_id")
    op.drop_table("live_vip_shows")
    postgresql.ENUM(name="live_vip_show_status").drop(op.get_bind(), checkfirst=True)
