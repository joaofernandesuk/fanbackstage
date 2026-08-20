"""Persist bounded, replay-safe subscription renewal attempts."""

import sqlalchemy as sa

from alembic import op

revision = "20260820_0007"
down_revision = "20260820_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "subscription_renewal_attempts",
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
            "subscription_period_id",
            sa.Uuid(),
            sa.ForeignKey("subscription_periods.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "payment_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("payment_attempts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "subscription_period_id", "attempt_number", name="uq_renewal_attempt_number"
        ),
        sa.UniqueConstraint("payment_attempt_id", name="uq_renewal_attempt_payment"),
    )
    op.create_index(
        "ix_subscription_renewal_attempts_subscription_period_id",
        "subscription_renewal_attempts",
        ["subscription_period_id"],
    )
    op.create_index(
        "ix_subscription_renewal_attempts_payment_attempt_id",
        "subscription_renewal_attempts",
        ["payment_attempt_id"],
    )
    op.create_index(
        "ix_subscription_renewal_attempts_next_retry_at",
        "subscription_renewal_attempts",
        ["next_retry_at"],
    )


def downgrade() -> None:
    op.drop_table("subscription_renewal_attempts")
