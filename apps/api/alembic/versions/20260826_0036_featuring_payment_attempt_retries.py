"""Persist replay-safe Featuring payment-attempt history.

Revision ID: 20260826_0036
Revises: 20260826_0035
"""

import sqlalchemy as sa

from alembic import op

revision = "20260826_0036"
down_revision = "20260826_0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "feature_booking_payment_attempts",
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
        sa.Column(
            "booking_id",
            sa.Uuid(),
            sa.ForeignKey("feature_bookings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "payment_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("payment_attempts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.CheckConstraint("attempt_number > 0", name="ck_feature_booking_attempt_positive_number"),
        sa.UniqueConstraint(
            "booking_id", "attempt_number", name="uq_feature_booking_attempt_number"
        ),
        sa.UniqueConstraint("payment_attempt_id", name="uq_feature_booking_attempt_payment"),
    )
    op.create_index(
        "ix_feature_booking_payment_attempts_booking_id",
        "feature_booking_payment_attempts",
        ["booking_id"],
    )
    op.create_index(
        "ix_feature_booking_payment_attempts_payment_attempt_id",
        "feature_booking_payment_attempts",
        ["payment_attempt_id"],
    )
    op.execute(
        sa.text(
            "INSERT INTO feature_booking_payment_attempts "
            "(id, booking_id, payment_attempt_id, attempt_number) "
            "SELECT gen_random_uuid(), id, payment_attempt_id, 1 "
            "FROM feature_bookings WHERE payment_attempt_id IS NOT NULL"
        )
    )


def downgrade() -> None:
    # Payment-attempt association history is financial evidence. A deployed
    # rollback must use a forward corrective migration that preserves it.
    has_history = (
        op.get_bind()
        .execute(sa.text("SELECT EXISTS (SELECT 1 FROM feature_booking_payment_attempts)"))
        .scalar_one()
    )
    if has_history:
        raise RuntimeError(
            "Cannot downgrade 0036 with Featuring payment history; "
            "use a forward corrective migration"
        )
    op.drop_table("feature_booking_payment_attempts")
