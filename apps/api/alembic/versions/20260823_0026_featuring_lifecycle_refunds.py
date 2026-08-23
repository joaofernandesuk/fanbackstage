"""Add durable featuring delivery and compensating refund records.

Revision ID: 20260823_0026
Revises: 20260823_0025
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260823_0026"
down_revision = "20260823_0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "feature_bookings",
        sa.Column("delivered_seconds", sa.Integer(), nullable=False, server_default="0"),
    )
    reason = postgresql.ENUM(
        "creator_ended",
        "platform_failure",
        "moderation_ineligible",
        "admin_disabled",
        name="feature_ineligibility_reason",
        create_type=False,
    )
    op.create_table(
        "feature_refunds",
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
            "booking_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("feature_bookings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reason", reason, nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column(
            "ledger_transaction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ledger_transactions.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.UniqueConstraint("booking_id", "reason", name="uq_feature_refund_booking_reason"),
        sa.CheckConstraint("amount_minor > 0", name="ck_feature_refund_positive"),
    )
    op.create_index("ix_feature_refunds_booking_id", "feature_refunds", ["booking_id"])


def downgrade() -> None:
    op.drop_index("ix_feature_refunds_booking_id", table_name="feature_refunds")
    op.drop_table("feature_refunds")
    op.drop_column("feature_bookings", "delivered_seconds")
