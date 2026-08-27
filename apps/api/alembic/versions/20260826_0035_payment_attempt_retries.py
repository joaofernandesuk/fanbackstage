"""Persist replay-safe retries, excess-capture liabilities and dispute states.

Downgrade is intentionally allowed only while the new financial tables are
empty. Once an excess capture is recorded, removing its immutable liability
would destroy financial history; production rollback must use a forward
corrective migration instead.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260826_0035"
down_revision = "20260826_0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TYPE ledger_transaction_type ADD VALUE IF NOT EXISTS 'excess_capture_liability'"
        )
    )
    op.execute(
        sa.text("ALTER TYPE ledger_transaction_type ADD VALUE IF NOT EXISTS 'payment_dispute_hold'")
    )
    op.execute(sa.text("ALTER TYPE subscription_period_status ADD VALUE IF NOT EXISTS 'disputed'"))
    op.execute(
        sa.text("ALTER TYPE subscription_period_status ADD VALUE IF NOT EXISTS 'chargeback'")
    )
    refund_requirement_status = postgresql.ENUM(
        "required",
        "completed",
        name="refund_requirement_status",
        create_type=False,
    )
    refund_requirement_status.create(op.get_bind(), checkfirst=True)
    excess_capture_source = postgresql.ENUM(
        "ppv_purchase",
        "subscription_period",
        "marketplace_order",
        "feature_booking",
        "message_unlock",
        "paid_message_send",
        "private_live_session",
        name="excess_capture_source",
        create_type=False,
    )
    excess_capture_source.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "purchase_payment_attempts",
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
            "payment_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("payment_attempts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "purchase_id",
            sa.Uuid(),
            sa.ForeignKey("purchases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.CheckConstraint("attempt_number > 0", name="ck_purchase_attempt_positive_number"),
        sa.UniqueConstraint("purchase_id", "attempt_number", name="uq_purchase_attempt_number"),
        sa.UniqueConstraint("payment_attempt_id", name="uq_purchase_attempt_payment"),
    )
    op.create_index(
        "ix_purchase_payment_attempts_purchase_id",
        "purchase_payment_attempts",
        ["purchase_id"],
    )
    op.create_index(
        "ix_purchase_payment_attempts_payment_attempt_id",
        "purchase_payment_attempts",
        ["payment_attempt_id"],
    )
    op.execute(
        sa.text(
            "INSERT INTO purchase_payment_attempts "
            "(id, purchase_id, payment_attempt_id, attempt_number) "
            "SELECT gen_random_uuid(), id, payment_attempt_id, 1 FROM purchases"
        )
    )
    op.create_table(
        "payment_refund_requirements",
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
            "payment_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("payment_attempts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_type", excess_capture_source, nullable=False),
        sa.Column("source_reference", sa.String(length=64), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "status",
            refund_requirement_status,
            nullable=False,
            server_default="required",
        ),
        sa.Column(
            "reason",
            sa.String(length=64),
            nullable=False,
            server_default="duplicate_capture",
        ),
        sa.Column(
            "liability_ledger_transaction_id",
            sa.Uuid(),
            sa.ForeignKey("ledger_transactions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "refund_ledger_transaction_id",
            sa.Uuid(),
            sa.ForeignKey("ledger_transactions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("provider_refund_reference", sa.String(length=255), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "amount_minor > 0", name="ck_payment_refund_requirement_positive_amount"
        ),
        sa.UniqueConstraint("payment_attempt_id", name="uq_payment_refund_requirement_attempt"),
        sa.UniqueConstraint(
            "liability_ledger_transaction_id",
            name="uq_payment_refund_requirement_liability_ledger",
        ),
        sa.UniqueConstraint(
            "refund_ledger_transaction_id",
            name="uq_payment_refund_requirement_refund_ledger",
        ),
        sa.UniqueConstraint(
            "provider_refund_reference",
            name="uq_payment_refund_requirement_provider_reference",
        ),
    )
    op.create_index(
        "ix_payment_refund_requirements_source_type",
        "payment_refund_requirements",
        ["source_type"],
    )
    op.create_index(
        "ix_payment_refund_requirements_source_reference",
        "payment_refund_requirements",
        ["source_reference"],
    )
    op.create_index(
        "ix_payment_refund_requirements_payment_attempt_id",
        "payment_refund_requirements",
        ["payment_attempt_id"],
    )
    op.create_index(
        "ix_payment_refund_requirements_status",
        "payment_refund_requirements",
        ["status"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    has_refund_requirements = bool(
        bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM payment_refund_requirements)"))
    )
    has_purchase_attempt_history = bool(
        bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM purchase_payment_attempts)"))
    )
    has_excess_capture_ledger = bool(
        bind.scalar(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM ledger_transactions "
                "WHERE transaction_type::text IN "
                "('excess_capture_liability', 'payment_dispute_hold'))"
            )
        )
    )
    has_terminal_subscription_periods = bool(
        bind.scalar(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM subscription_periods "
                "WHERE status::text IN ('disputed', 'chargeback'))"
            )
        )
    )
    if (
        has_refund_requirements
        or has_purchase_attempt_history
        or has_excess_capture_ledger
        or has_terminal_subscription_periods
    ):
        raise RuntimeError(
            "Cannot downgrade 20260826_0035 after payment-attempt or excess-capture "
            "financial history exists; deploy a forward corrective migration instead"
        )

    op.drop_table("payment_refund_requirements")
    op.drop_table("purchase_payment_attempts")
    op.execute(
        sa.text(
            "ALTER TABLE ledger_transactions ALTER COLUMN transaction_type TYPE text "
            "USING transaction_type::text"
        )
    )
    op.execute(sa.text("DROP TYPE ledger_transaction_type"))
    op.execute(
        sa.text(
            "CREATE TYPE ledger_transaction_type AS ENUM ("
            "'ppv_purchase', 'earnings_release', 'refund', 'chargeback', "
            "'subscription_charge', 'messaging_charge', 'private_live_session', "
            "'marketplace_order', 'featuring_charge')"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE ledger_transactions ALTER COLUMN transaction_type "
            "TYPE ledger_transaction_type USING transaction_type::ledger_transaction_type"
        )
    )
    op.execute(
        sa.text("ALTER TABLE subscription_periods ALTER COLUMN status TYPE text USING status::text")
    )
    op.execute(sa.text("DROP TYPE subscription_period_status"))
    op.execute(
        sa.text(
            "CREATE TYPE subscription_period_status AS ENUM ("
            "'pending', 'active', 'failed', 'refunded')"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE subscription_periods ALTER COLUMN status "
            "TYPE subscription_period_status USING status::subscription_period_status"
        )
    )
    op.execute(sa.text("DROP TYPE IF EXISTS excess_capture_source"))
    op.execute(sa.text("DROP TYPE IF EXISTS refund_requirement_status"))
