"""Add delivery-based marketplace earnings hold policy and order snapshots."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260821_0016"
down_revision = "20260821_0015"
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
    op.execute(
        "CREATE TYPE marketplace_seller_tier AS ENUM ('trusted', 'standard', 'new_seller', 'high_risk')"
    )
    op.execute(
        "CREATE TYPE marketplace_earnings_release_status AS ENUM ('pending', 'blocked', 'released')"
    )
    op.create_table(
        "marketplace_seller_risk_profiles",
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "tier",
            postgresql.ENUM(name="marketplace_seller_tier", create_type=False),
            nullable=False,
            server_default="new_seller",
        ),
        sa.Column("marketplace_suspended", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
    )
    op.create_index(
        "ix_marketplace_seller_risk_profiles_creator_id",
        "marketplace_seller_risk_profiles",
        ["creator_id"],
        unique=True,
    )
    op.create_index(
        "ix_marketplace_seller_risk_profiles_tier", "marketplace_seller_risk_profiles", ["tier"]
    )
    op.create_table(
        "marketplace_earnings_hold_policies",
        sa.Column(
            "seller_tier",
            postgresql.ENUM(name="marketplace_seller_tier", create_type=False),
            nullable=False,
        ),
        sa.Column("hold_duration_seconds", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        *_timestamps(),
        sa.CheckConstraint("hold_duration_seconds >= 0", name="ck_marketplace_hold_nonnegative"),
        sa.UniqueConstraint("seller_tier", name="uq_marketplace_hold_policy_tier"),
    )
    op.create_index(
        "ix_marketplace_earnings_hold_policies_seller_tier",
        "marketplace_earnings_hold_policies",
        ["seller_tier"],
    )
    # Initial policy is persisted configuration, not a hardcoded service rule.
    op.execute(
        "INSERT INTO marketplace_earnings_hold_policies "
        "(id, seller_tier, hold_duration_seconds, active, is_default) VALUES "
        "('00000000-0000-0000-0000-000000000161', 'trusted', 172800, true, false), "
        "('00000000-0000-0000-0000-000000000162', 'standard', 604800, true, true), "
        "('00000000-0000-0000-0000-000000000163', 'new_seller', 1209600, true, false), "
        "('00000000-0000-0000-0000-000000000164', 'high_risk', 1814400, true, false)"
    )
    op.add_column("marketplace_orders", sa.Column("shipped_at", sa.DateTime(timezone=True)))
    op.add_column("marketplace_orders", sa.Column("delivered_at", sa.DateTime(timezone=True)))
    op.add_column("marketplace_orders", sa.Column("tracking_reference", sa.String(255)))
    op.add_column(
        "marketplace_orders",
        sa.Column(
            "seller_tier_snapshot",
            postgresql.ENUM(name="marketplace_seller_tier", create_type=False),
        ),
    )
    op.add_column("marketplace_orders", sa.Column("hold_duration_seconds_snapshot", sa.Integer()))
    op.add_column(
        "marketplace_orders", sa.Column("earnings_hold_until", sa.DateTime(timezone=True))
    )
    op.add_column(
        "marketplace_orders", sa.Column("earnings_released_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "marketplace_orders",
        sa.Column(
            "earnings_release_status",
            postgresql.ENUM(name="marketplace_earnings_release_status", create_type=False),
            nullable=False,
            server_default="pending",
        ),
    )
    op.add_column("marketplace_orders", sa.Column("release_block_reason", sa.String(255)))
    op.create_index(
        "ix_marketplace_orders_earnings_hold_until", "marketplace_orders", ["earnings_hold_until"]
    )
    op.create_index(
        "ix_marketplace_orders_earnings_release_status",
        "marketplace_orders",
        ["earnings_release_status"],
    )


def downgrade() -> None:
    op.drop_index("ix_marketplace_orders_earnings_release_status", table_name="marketplace_orders")
    op.drop_index("ix_marketplace_orders_earnings_hold_until", table_name="marketplace_orders")
    for name in (
        "release_block_reason",
        "earnings_release_status",
        "earnings_released_at",
        "earnings_hold_until",
        "hold_duration_seconds_snapshot",
        "seller_tier_snapshot",
        "tracking_reference",
        "delivered_at",
        "shipped_at",
    ):
        op.drop_column("marketplace_orders", name)
    op.drop_table("marketplace_earnings_hold_policies")
    op.drop_table("marketplace_seller_risk_profiles")
    op.execute("DROP TYPE marketplace_earnings_release_status")
    op.execute("DROP TYPE marketplace_seller_tier")
