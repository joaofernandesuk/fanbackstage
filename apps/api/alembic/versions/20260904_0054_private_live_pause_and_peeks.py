"""Add resumable public Live pauses and payment-gated private peeks.

Revision ID: 20260904_0054
Revises: 20260903_0053
Create Date: 2026-09-04

The new PostgreSQL enum labels are intentionally forward-only. Historical
ledger and payment rows may retain them after a schema rollback.
"""

import sqlalchemy as sa

from alembic import op

revision = "20260904_0054"
down_revision = "20260903_0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE live_commerce_kind ADD VALUE IF NOT EXISTS 'private_peek'")
    op.execute("ALTER TYPE ledger_transaction_type ADD VALUE IF NOT EXISTS 'private_live_peek'")
    op.execute("ALTER TYPE excess_capture_source ADD VALUE IF NOT EXISTS 'private_live_peek'")

    op.add_column(
        "creator_live_settings",
        sa.Column("private_peeks_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("private_sessions", sa.Column("public_live_room_id", sa.UUID()))
    op.add_column(
        "private_sessions",
        sa.Column("peeks_allowed", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column("private_sessions", sa.Column("peek_price_minor", sa.Integer()))
    op.add_column("private_sessions", sa.Column("peek_currency", sa.String(length=3)))
    op.add_column(
        "private_sessions", sa.Column("peek_commission_basis_points", sa.Integer())
    )
    op.create_check_constraint(
        "ck_private_session_peek_terms",
        "private_sessions",
        "(NOT peeks_allowed) OR (peek_price_minor > 0 AND peek_currency IS NOT NULL "
        "AND peek_commission_basis_points BETWEEN 0 AND 10000)",
    )
    op.create_foreign_key(
        "fk_private_session_public_live_room",
        "private_sessions",
        "live_rooms",
        ["public_live_room_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_private_sessions_public_live_room_id", "private_sessions", ["public_live_room_id"]
    )

    op.add_column("live_rooms", sa.Column("active_private_session_id", sa.UUID()))
    op.add_column("live_rooms", sa.Column("private_paused_at", sa.DateTime(timezone=True)))
    op.create_foreign_key(
        "fk_live_room_active_private_session",
        "live_rooms",
        "private_sessions",
        ["active_private_session_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_live_rooms_active_private_session_id", "live_rooms", ["active_private_session_id"]
    )

    op.create_table(
        "live_private_peek_policies",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False, unique=True),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("commission_basis_points", sa.Integer(), nullable=False),
        sa.CheckConstraint("amount_minor > 0", name="ck_live_private_peek_policy_amount"),
        sa.CheckConstraint(
            "commission_basis_points BETWEEN 0 AND 10000",
            name="ck_live_private_peek_policy_commission",
        ),
    )

    op.add_column("live_commerce_charges", sa.Column("private_session_id", sa.UUID()))
    op.create_foreign_key(
        "fk_live_commerce_charge_private_session",
        "live_commerce_charges",
        "private_sessions",
        ["private_session_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_live_commerce_charges_private_session_id",
        "live_commerce_charges",
        ["private_session_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_live_commerce_charges_private_session_id", table_name="live_commerce_charges")
    op.drop_constraint(
        "fk_live_commerce_charge_private_session", "live_commerce_charges", type_="foreignkey"
    )
    op.drop_column("live_commerce_charges", "private_session_id")
    op.drop_table("live_private_peek_policies")
    op.drop_index("ix_live_rooms_active_private_session_id", table_name="live_rooms")
    op.drop_constraint("fk_live_room_active_private_session", "live_rooms", type_="foreignkey")
    op.drop_column("live_rooms", "private_paused_at")
    op.drop_column("live_rooms", "active_private_session_id")
    op.drop_index("ix_private_sessions_public_live_room_id", table_name="private_sessions")
    op.drop_constraint("fk_private_session_public_live_room", "private_sessions", type_="foreignkey")
    op.drop_constraint("ck_private_session_peek_terms", "private_sessions", type_="check")
    op.drop_column("private_sessions", "peek_commission_basis_points")
    op.drop_column("private_sessions", "peek_currency")
    op.drop_column("private_sessions", "peek_price_minor")
    op.drop_column("private_sessions", "peeks_allowed")
    op.drop_column("private_sessions", "public_live_room_id")
    op.drop_column("creator_live_settings", "private_peeks_enabled")
