"""Add Live commerce catalog, creator configuration, and frozen charges.

This revision is intentionally forward-only. Once payment-backed Live charges
or their settlement references exist, dropping the schema would make immutable
financial/audit history unrecoverable. A release rollback keeps the compatible
application image and uses a corrective forward migration when required.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260831_0046"
down_revision = "20260831_0045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    kind = postgresql.ENUM(
        "tip", "gift", "paid_request", name="live_commerce_kind", create_type=False
    )
    status = postgresql.ENUM(
        "pending_payment",
        "paid_pending_creator",
        "accepted",
        "declined",
        "completed",
        "refunded",
        "expired",
        "disputed",
        name="live_commerce_status",
        create_type=False,
    )
    kind.create(bind, checkfirst=True)
    status.create(bind, checkfirst=True)
    op.execute("ALTER TYPE ledger_transaction_type ADD VALUE IF NOT EXISTS 'live_tip'")
    op.execute("ALTER TYPE ledger_transaction_type ADD VALUE IF NOT EXISTS 'live_gift'")
    op.execute("ALTER TYPE ledger_transaction_type ADD VALUE IF NOT EXISTS 'live_paid_request'")
    op.create_table(
        "live_gift_catalog_items",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("name", sa.String(80), nullable=False, unique=True),
        sa.Column("icon", sa.String(120), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("category", sa.String(48)),
        sa.CheckConstraint("amount_minor > 0", name="ck_live_gift_amount_positive"),
        sa.CheckConstraint("btrim(name) <> ''", name="ck_live_gift_name"),
    )
    op.create_index("ix_live_gift_catalog_items_active", "live_gift_catalog_items", ["active"])
    op.create_table(
        "live_tip_menu_items",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "creator_id",
            sa.UUID(),
            sa.ForeignKey("creator_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("amount_minor > 0", name="ck_live_tip_menu_amount_positive"),
        sa.CheckConstraint("btrim(label) <> ''", name="ck_live_tip_menu_label"),
        sa.UniqueConstraint("creator_id", "sort_order", name="uq_live_tip_menu_creator_order"),
    )
    op.create_index("ix_live_tip_menu_items_creator_id", "live_tip_menu_items", ["creator_id"])
    op.create_table(
        "live_paid_request_options",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("creator_id", sa.UUID(), sa.ForeignKey("creator_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("amount_minor > 0", name="ck_live_paid_request_option_amount_positive"),
        sa.CheckConstraint("btrim(label) <> ''", name="ck_live_paid_request_option_label"),
    )
    op.create_index("ix_live_paid_request_options_creator_id", "live_paid_request_options", ["creator_id"])
    op.create_table(
        "live_goals",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "creator_id",
            sa.UUID(),
            sa.ForeignKey("creator_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(140), nullable=False),
        sa.Column("target_amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("target_amount_minor > 0", name="ck_live_goal_target_positive"),
        sa.CheckConstraint("btrim(title) <> ''", name="ck_live_goal_title"),
    )
    op.create_index("ix_live_goals_creator_id", "live_goals", ["creator_id"])
    op.create_index("ix_live_goals_active", "live_goals", ["active"])
    op.create_table(
        "live_commerce_charges",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "live_room_id",
            sa.UUID(),
            sa.ForeignKey("live_rooms.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "creator_id",
            sa.UUID(),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "buyer_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("kind", kind, nullable=False),
        sa.Column("status", status, nullable=False),
        sa.Column(
            "gift_catalog_item_id",
            sa.UUID(),
            sa.ForeignKey("live_gift_catalog_items.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "tip_menu_item_id",
            sa.UUID(),
            sa.ForeignKey("live_tip_menu_items.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "paid_request_option_id",
            sa.UUID(),
            sa.ForeignKey("live_paid_request_options.id", ondelete="RESTRICT"),
        ),
        sa.Column("request_label", sa.String(100)),
        sa.Column("request_message", sa.String(500)),
        sa.Column("gross_amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("commission_basis_points", sa.Integer(), nullable=False),
        sa.Column(
            "payment_attempt_id",
            sa.UUID(),
            sa.ForeignKey("payment_attempts.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "ledger_transaction_id",
            sa.UUID(),
            sa.ForeignKey("ledger_transactions.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("gross_amount_minor > 0", name="ck_live_charge_gross_positive"),
        sa.CheckConstraint("commission_basis_points >= 0", name="ck_live_charge_commission"),
    )
    op.create_index(
        "ix_live_commerce_charges_live_room_id", "live_commerce_charges", ["live_room_id"]
    )
    op.create_index("ix_live_commerce_charges_creator_id", "live_commerce_charges", ["creator_id"])
    op.create_index(
        "ix_live_charge_room_status",
        "live_commerce_charges",
        ["live_room_id", "status", "created_at"],
    )
    op.create_index("ix_live_commerce_charges_expires_at", "live_commerce_charges", ["expires_at"])


def downgrade() -> None:
    """Do not destructively remove immutable Live-commerce history."""
    raise RuntimeError(
        "Live commerce uses immutable financial history; use a forward corrective migration"
    )
