"""Add immutable referral commission allocations and beneficiary ledger accounts."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260822_0020"
down_revision = "5a8c53c69479"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for value in (
        "referrer_pending",
        "referrer_available",
        "affiliate_pending",
        "affiliate_available",
    ):
        op.execute(f"ALTER TYPE ledger_account_kind ADD VALUE IF NOT EXISTS '{value}'")
    op.add_column("ledger_accounts", sa.Column("owner_user_id", sa.Uuid(), nullable=True))
    op.add_column(
        "ledger_accounts", sa.Column("owner_affiliate_partner_id", sa.Uuid(), nullable=True)
    )
    op.create_foreign_key(
        "fk_ledger_accounts_owner_user",
        "ledger_accounts",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_ledger_accounts_owner_affiliate",
        "ledger_accounts",
        "affiliate_partners",
        ["owner_affiliate_partner_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_ledger_accounts_owner_user_id", "ledger_accounts", ["owner_user_id"])
    op.create_index(
        "ix_ledger_accounts_owner_affiliate_partner_id",
        "ledger_accounts",
        ["owner_affiliate_partner_id"],
    )
    op.drop_index("uq_ledger_platform_account_kind_currency", table_name="ledger_accounts")
    op.create_index(
        "uq_ledger_platform_account_kind_currency",
        "ledger_accounts",
        ["kind", "currency"],
        unique=True,
        postgresql_where=sa.text(
            "owner_creator_id IS NULL AND owner_group_id IS NULL AND owner_user_id IS NULL AND owner_affiliate_partner_id IS NULL"
        ),
    )
    op.create_index(
        "uq_ledger_user_account_kind_currency",
        "ledger_accounts",
        ["owner_user_id", "kind", "currency"],
        unique=True,
        postgresql_where=sa.text("owner_user_id IS NOT NULL"),
    )
    op.create_index(
        "uq_ledger_affiliate_account_kind_currency",
        "ledger_accounts",
        ["owner_affiliate_partner_id", "kind", "currency"],
        unique=True,
        postgresql_where=sa.text("owner_affiliate_partner_id IS NOT NULL"),
    )
    op.create_table(
        "referral_commission_allocations",
        sa.Column(
            "source_ledger_transaction_id",
            sa.Uuid(),
            sa.ForeignKey("ledger_transactions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
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
        sa.Column(
            "beneficiary_actor_type",
            postgresql.ENUM(name="referral_actor_type", create_type=False),
            nullable=False,
        ),
        sa.Column("beneficiary_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column(
            "beneficiary_creator_id",
            sa.Uuid(),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "beneficiary_affiliate_partner_id",
            sa.Uuid(),
            sa.ForeignKey("affiliate_partners.id", ondelete="RESTRICT"),
        ),
        sa.Column("revenue_type", sa.String(64), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("platform_fee_minor", sa.Integer(), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("policy_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("allocated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("reversed_at", sa.DateTime(timezone=True)),
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
        sa.CheckConstraint("amount_minor >= 0", name="ck_referral_allocation_nonnegative_amount"),
        sa.UniqueConstraint(
            "source_ledger_transaction_id", name="uq_referral_allocation_source_ledger"
        ),
    )
    op.create_index(
        "ix_referral_allocation_attribution",
        "referral_commission_allocations",
        ["signup_attribution_id"],
    )


def downgrade() -> None:
    op.drop_table("referral_commission_allocations")
    op.drop_index("uq_ledger_affiliate_account_kind_currency", table_name="ledger_accounts")
    op.drop_index("uq_ledger_user_account_kind_currency", table_name="ledger_accounts")
    op.drop_index("uq_ledger_platform_account_kind_currency", table_name="ledger_accounts")
    op.create_index(
        "uq_ledger_platform_account_kind_currency",
        "ledger_accounts",
        ["kind", "currency"],
        unique=True,
        postgresql_where=sa.text("owner_creator_id IS NULL"),
    )
    op.drop_index("ix_ledger_accounts_owner_affiliate_partner_id", table_name="ledger_accounts")
    op.drop_index("ix_ledger_accounts_owner_user_id", table_name="ledger_accounts")
    op.drop_constraint("fk_ledger_accounts_owner_affiliate", "ledger_accounts", type_="foreignkey")
    op.drop_constraint("fk_ledger_accounts_owner_user", "ledger_accounts", type_="foreignkey")
    op.drop_column("ledger_accounts", "owner_affiliate_partner_id")
    op.drop_column("ledger_accounts", "owner_user_id")
