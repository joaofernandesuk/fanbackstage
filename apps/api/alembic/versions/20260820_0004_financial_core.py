"""Phase 3 financial core.

Ledger history is append-only. Production rollback must use a forward corrective
migration; this downgrade removes financial history.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260820_0004"
down_revision = "20260820_0003"
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
    op.add_column("content_items", sa.Column("price_amount_minor", sa.Integer()))
    op.add_column("content_items", sa.Column("price_currency", sa.String(3)))
    op.create_check_constraint(
        "ck_content_price_valid",
        "content_items",
        "price_amount_minor IS NULL OR (price_amount_minor > 0 AND price_currency IS NOT NULL)",
    )
    op.create_table(
        "ledger_accounts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *timestamps(),
        sa.Column(
            "owner_creator_id", sa.Uuid(), sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT")
        ),
        sa.Column(
            "kind",
            enum(
                "ledger_account_kind",
                [
                    "platform_clearing",
                    "platform_revenue",
                    "creator_pending",
                    "creator_available",
                    "refund_clearing",
                ],
            ),
            nullable=False,
        ),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.UniqueConstraint(
            "owner_creator_id", "kind", "currency", name="uq_ledger_account_owner_kind_currency"
        ),
    )
    op.create_table(
        "ledger_transactions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *timestamps(),
        sa.Column(
            "transaction_type",
            enum("ledger_transaction_type", ["ppv_purchase", "earnings_release", "refund"]),
            nullable=False,
        ),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("reference", sa.String(255), nullable=False),
        sa.Column(
            "reversal_of_transaction_id",
            sa.Uuid(),
            sa.ForeignKey("ledger_transactions.id", ondelete="RESTRICT"),
        ),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.UniqueConstraint("idempotency_key", name="uq_ledger_transactions_idempotency"),
        sa.UniqueConstraint("reference"),
    )
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *timestamps(),
        sa.Column(
            "transaction_id",
            sa.Uuid(),
            sa.ForeignKey("ledger_transactions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ledger_account_id",
            sa.Uuid(),
            sa.ForeignKey("ledger_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("direction", enum("ledger_direction", ["debit", "credit"]), nullable=False),
        sa.CheckConstraint("amount_minor > 0", name="ck_ledger_entries_positive_amount"),
    )
    op.create_table(
        "commission_rules",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *timestamps(),
        sa.Column("revenue_type", sa.String(64), nullable=False),
        sa.Column("basis_points", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint(
            "basis_points >= 0 AND basis_points <= 10000", name="ck_commission_rule_bps"
        ),
        sa.UniqueConstraint("revenue_type"),
    )
    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *timestamps(),
        sa.Column(
            "buyer_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_reference", sa.String(255), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "status",
            enum(
                "payment_status",
                ["pending", "succeeded", "failed", "refunded", "disputed", "chargeback"],
            ),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("amount_minor > 0", name="ck_payment_attempt_positive_amount"),
        sa.UniqueConstraint(
            "provider", "provider_reference", name="uq_payment_attempt_provider_reference"
        ),
        sa.UniqueConstraint(
            "buyer_user_id", "idempotency_key", name="uq_payment_attempt_buyer_idempotency"
        ),
    )
    op.create_table(
        "purchases",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *timestamps(),
        sa.Column(
            "buyer_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "seller_creator_id",
            sa.Uuid(),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "content_id",
            sa.Uuid(),
            sa.ForeignKey("content_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "payment_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("payment_attempts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("gross_amount_minor", sa.Integer(), nullable=False),
        sa.Column("platform_fee_minor", sa.Integer(), nullable=False),
        sa.Column("creator_amount_minor", sa.Integer(), nullable=False),
        sa.Column("commission_basis_points", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "status",
            enum(
                "purchase_status",
                ["awaiting_payment", "paid", "failed", "refunded", "disputed", "chargeback"],
            ),
            nullable=False,
        ),
        sa.Column(
            "entitlement_id",
            sa.Uuid(),
            sa.ForeignKey("content_entitlements.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "ledger_transaction_id",
            sa.Uuid(),
            sa.ForeignKey("ledger_transactions.id", ondelete="RESTRICT"),
        ),
        sa.Column("purchased_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("gross_amount_minor > 0", name="ck_purchase_positive_gross"),
        sa.CheckConstraint("platform_fee_minor >= 0", name="ck_purchase_nonnegative_fee"),
        sa.CheckConstraint(
            "creator_amount_minor >= 0", name="ck_purchase_nonnegative_creator_amount"
        ),
        sa.CheckConstraint(
            "gross_amount_minor = platform_fee_minor + creator_amount_minor",
            name="ck_purchase_amounts_balance",
        ),
        sa.CheckConstraint(
            "commission_basis_points >= 0 AND commission_basis_points <= 10000",
            name="ck_purchase_bps",
        ),
        sa.UniqueConstraint("buyer_user_id", "content_id", name="uq_purchase_buyer_content"),
        sa.UniqueConstraint("payment_attempt_id"),
        sa.UniqueConstraint("entitlement_id"),
        sa.UniqueConstraint("ledger_transaction_id"),
    )
    op.create_table(
        "payment_webhook_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *timestamps(),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("external_event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column(
            "payment_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("payment_attempts.id", ondelete="SET NULL"),
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "provider", "external_event_id", name="uq_payment_webhook_provider_event"
        ),
    )
    for table, column in [
        ("ledger_accounts", "owner_creator_id"),
        ("ledger_transactions", "transaction_type"),
        ("ledger_entries", "transaction_id"),
        ("ledger_entries", "ledger_account_id"),
        ("payment_attempts", "buyer_user_id"),
        ("payment_attempts", "status"),
        ("purchases", "buyer_user_id"),
        ("purchases", "seller_creator_id"),
        ("purchases", "content_id"),
        ("purchases", "status"),
    ]:
        op.create_index(f"ix_{table}_{column}", table, [column])
    op.create_index(
        "uq_ledger_platform_account_kind_currency",
        "ledger_accounts",
        ["kind", "currency"],
        unique=True,
        postgresql_where=sa.text("owner_creator_id IS NULL"),
    )
    op.execute(
        """
        CREATE FUNCTION prevent_financial_history_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'Financial ledger history is append-only';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in ("ledger_transactions", "ledger_entries"):
        op.execute(
            f"CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION prevent_financial_history_mutation()"
        )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS prevent_financial_history_mutation() CASCADE")
    op.execute("DROP INDEX IF EXISTS uq_ledger_platform_account_kind_currency")
    for table, column in [
        ("purchases", "status"),
        ("purchases", "content_id"),
        ("purchases", "seller_creator_id"),
        ("purchases", "buyer_user_id"),
        ("payment_attempts", "status"),
        ("payment_attempts", "buyer_user_id"),
        ("ledger_entries", "ledger_account_id"),
        ("ledger_entries", "transaction_id"),
        ("ledger_transactions", "transaction_type"),
        ("ledger_accounts", "owner_creator_id"),
    ]:
        op.drop_index(f"ix_{table}_{column}", table_name=table)
    for table in (
        "payment_webhook_events",
        "purchases",
        "payment_attempts",
        "commission_rules",
        "ledger_entries",
        "ledger_transactions",
        "ledger_accounts",
    ):
        op.drop_table(table)
    for name in (
        "purchase_status",
        "payment_status",
        "ledger_direction",
        "ledger_transaction_type",
        "ledger_account_kind",
    ):
        op.execute(f"DROP TYPE IF EXISTS {name}")
    op.drop_constraint("ck_content_price_valid", "content_items", type_="check")
    op.drop_column("content_items", "price_currency")
    op.drop_column("content_items", "price_amount_minor")
