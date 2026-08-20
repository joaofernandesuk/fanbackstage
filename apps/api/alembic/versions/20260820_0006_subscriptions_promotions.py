"""Phase 4 subscriptions and promotions.

Subscription periods are append-only commercial snapshots.  Downgrade removes
Phase 4 subscription history and is therefore only appropriate for local/test
databases; production rollback must use a forward corrective migration.
"""

import sqlalchemy as sa

from alembic import op

revision = "20260820_0006"
down_revision = "20260820_0005"
branch_labels = None
depends_on = None


def enum(name: str, values: list[str]) -> sa.Enum:
    return sa.Enum(*values, name=name)


def timestamps() -> list[sa.Column]:
    return [
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
    op.execute("ALTER TYPE ledger_transaction_type ADD VALUE IF NOT EXISTS 'subscription_charge'")
    op.alter_column("content_entitlements", "content_id", existing_type=sa.Uuid(), nullable=True)
    op.add_column(
        "content_entitlements",
        sa.Column(
            "creator_id", sa.Uuid(), sa.ForeignKey("creator_profiles.id", ondelete="CASCADE")
        ),
    )
    op.create_index("ix_content_entitlements_creator_id", "content_entitlements", ["creator_id"])
    op.create_check_constraint(
        "ck_entitlement_single_scope",
        "content_entitlements",
        "(content_id IS NOT NULL AND creator_id IS NULL) OR (content_id IS NULL AND creator_id IS NOT NULL)",
    )
    duration = enum("subscription_duration", ["month_1", "month_3", "month_6", "month_12"])
    eligibility = enum("promotion_eligibility", ["new_subscriber", "all_eligible", "reactivation"])
    scope = enum("promotion_renewal_scope", ["initial_only", "initial_and_renewal"])
    status = enum(
        "subscription_status",
        ["pending", "active", "grace_period", "payment_failed", "expired", "suspended"],
    )
    period_status = enum("subscription_period_status", ["pending", "active", "failed", "refunded"])
    op.create_table(
        "subscription_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *timestamps(),
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "subscription_plan_prices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *timestamps(),
        sa.Column(
            "plan_id",
            sa.Uuid(),
            sa.ForeignKey("subscription_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("duration", duration, nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint("amount_minor > 0", name="ck_subscription_plan_positive_price"),
        sa.UniqueConstraint("plan_id", "duration", name="uq_subscription_plan_duration"),
    )
    op.create_table(
        "subscription_promotions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *timestamps(),
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("eligibility", eligibility, nullable=False),
        sa.Column("renewal_scope", scope, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "end_at IS NULL OR end_at > start_at", name="ck_subscription_promotion_dates"
        ),
    )
    op.create_table(
        "subscription_promotion_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *timestamps(),
        sa.Column(
            "promotion_id",
            sa.Uuid(),
            sa.ForeignKey("subscription_promotions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("duration", duration, nullable=False),
        sa.Column("discount_basis_points", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "discount_basis_points >= 0 AND discount_basis_points < 10000",
            name="ck_subscription_promotion_discount",
        ),
        sa.UniqueConstraint("promotion_id", "duration", name="uq_subscription_promotion_duration"),
    )
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *timestamps(),
        sa.Column(
            "subscriber_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "plan_id",
            sa.Uuid(),
            sa.ForeignKey("subscription_plans.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("duration", duration, nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column("auto_renew", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("cancel_at_period_end", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("current_period_start", sa.DateTime(timezone=True)),
        sa.Column("current_period_end", sa.DateTime(timezone=True)),
        sa.Column("grace_period_end", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
    )
    op.create_table(
        "subscription_periods",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *timestamps(),
        sa.Column(
            "subscription_id",
            sa.Uuid(),
            sa.ForeignKey("subscriptions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("status", period_status, nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration", duration, nullable=False),
        sa.Column("base_amount_minor", sa.Integer(), nullable=False),
        sa.Column("discount_amount_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("charged_amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "promotion_id",
            sa.Uuid(),
            sa.ForeignKey("subscription_promotions.id", ondelete="RESTRICT"),
        ),
        sa.Column("promotion_eligibility", eligibility),
        sa.Column("discount_basis_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("commission_basis_points", sa.Integer(), nullable=False),
        sa.Column("platform_fee_minor", sa.Integer(), nullable=False),
        sa.Column("creator_amount_minor", sa.Integer(), nullable=False),
        sa.Column(
            "payment_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("payment_attempts.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "ledger_transaction_id",
            sa.Uuid(),
            sa.ForeignKey("ledger_transactions.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.Column(
            "entitlement_id",
            sa.Uuid(),
            sa.ForeignKey("content_entitlements.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.CheckConstraint(
            "base_amount_minor > 0 AND charged_amount_minor > 0",
            name="ck_subscription_period_positive_amounts",
        ),
        sa.CheckConstraint(
            "base_amount_minor = discount_amount_minor + charged_amount_minor",
            name="ck_subscription_period_discount_balance",
        ),
        sa.UniqueConstraint("subscription_id", "sequence", name="uq_subscription_period_sequence"),
    )
    for table, col in [
        ("subscription_plans", "creator_id"),
        ("subscription_plan_prices", "plan_id"),
        ("subscription_promotions", "creator_id"),
        ("subscription_promotions", "enabled"),
        ("subscription_promotion_rules", "promotion_id"),
        ("subscriptions", "subscriber_user_id"),
        ("subscriptions", "creator_id"),
        ("subscriptions", "status"),
        ("subscription_periods", "subscription_id"),
        ("subscription_periods", "status"),
    ]:
        op.create_index(f"ix_{table}_{col}", table, [col])
    op.create_index("ix_subscription_due", "subscriptions", ["status", "current_period_end"])


def downgrade() -> None:
    for table in (
        "subscription_periods",
        "subscriptions",
        "subscription_promotion_rules",
        "subscription_promotions",
        "subscription_plan_prices",
        "subscription_plans",
    ):
        op.drop_table(table)
    for name in (
        "subscription_period_status",
        "subscription_status",
        "promotion_renewal_scope",
        "promotion_eligibility",
        "subscription_duration",
    ):
        op.execute(f"DROP TYPE IF EXISTS {name}")
    op.drop_constraint("ck_entitlement_single_scope", "content_entitlements", type_="check")
    op.drop_index("ix_content_entitlements_creator_id", table_name="content_entitlements")
    op.drop_column("content_entitlements", "creator_id")
    op.alter_column("content_entitlements", "content_id", existing_type=sa.Uuid(), nullable=False)
