"""Add immutable timestamp-based subscription referral reward windows."""

import sqlalchemy as sa

from alembic import op

revision = "20260822_0021"
down_revision = "20260822_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "referral_subscription_reward_windows",
        sa.Column(
            "signup_attribution_id",
            sa.Uuid(),
            sa.ForeignKey("signup_attributions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "policy_id",
            sa.Uuid(),
            sa.ForeignKey("referral_commission_policies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("first_successful_payment_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reward_window_ends_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.UniqueConstraint(
            "signup_attribution_id", name="uq_referral_subscription_reward_window_attribution"
        ),
    )
    op.create_index(
        "ix_referral_subscription_reward_windows_signup_attribution_id",
        "referral_subscription_reward_windows",
        ["signup_attribution_id"],
    )
    op.create_index(
        "ix_referral_subscription_reward_windows_reward_window_ends_at",
        "referral_subscription_reward_windows",
        ["reward_window_ends_at"],
    )


def downgrade() -> None:
    op.drop_table("referral_subscription_reward_windows")
