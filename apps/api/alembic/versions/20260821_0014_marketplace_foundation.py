"""Add Phase 9 physical marketplace and server-owned shipping allowances.

The financial fields on marketplace_orders are checkout/settlement snapshots.
They deliberately preserve allowance and commission treatment after later
configuration or group-contract changes.
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260821_0014"
down_revision = "20260821_0013"
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
    for name, values in {
        "marketplace_listing_status": "draft, pending_review, published, paused, sold_out, rejected, removed, archived",
        "marketplace_condition": "new, like_new, used, personal_worn",
        "marketplace_shipping_mode": "domestic, selected_countries, worldwide",
        "shipping_allowance_scope": "country, region",
        "marketplace_order_status": "awaiting_payment, paid, processing, shipped, delivered, cancelled, refunded, disputed, chargeback",
    }.items():
        op.execute(
            f"CREATE TYPE {name} AS ENUM ({', '.join(repr(value.strip()) for value in values.split(','))})"
        )
    op.execute("ALTER TYPE ledger_transaction_type ADD VALUE IF NOT EXISTS 'marketplace_order'")
    op.execute("ALTER TYPE group_permission ADD VALUE IF NOT EXISTS 'manage_marketplace'")
    op.execute("ALTER TYPE group_permission ADD VALUE IF NOT EXISTS 'manage_marketplace_orders'")

    op.create_table(
        "marketplace_listings",
        sa.Column("public_id", sa.String(48), nullable=False, unique=True),
        sa.Column(
            "owner_creator_id",
            sa.Uuid(),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("category", sa.String(80), nullable=False),
        sa.Column(
            "condition",
            postgresql.ENUM(name="marketplace_condition", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="marketplace_listing_status", create_type=False),
            nullable=False,
            server_default="draft",
        ),
        sa.Column(
            "moderation_status",
            postgresql.ENUM(name="moderation_status", create_type=False),
            nullable=False,
            server_default="not_reviewed",
        ),
        sa.Column("quantity_available", sa.Integer(), nullable=False),
        sa.Column("price_amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column(
            "shipping_mode",
            postgresql.ENUM(name="marketplace_shipping_mode", create_type=False),
            nullable=False,
        ),
        sa.Column("origin_country_code", sa.String(2), nullable=False),
        sa.Column("shipping_charged_minor", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "quantity_available >= 0", name="ck_marketplace_listing_nonnegative_stock"
        ),
        sa.CheckConstraint("price_amount_minor > 0", name="ck_marketplace_listing_positive_price"),
        sa.CheckConstraint(
            "shipping_charged_minor >= 0", name="ck_marketplace_listing_nonnegative_shipping"
        ),
    )
    op.create_index(
        "ix_marketplace_listings_public_id", "marketplace_listings", ["public_id"], unique=True
    )
    op.create_index(
        "ix_marketplace_listings_owner_creator_id", "marketplace_listings", ["owner_creator_id"]
    )
    op.create_index(
        "ix_marketplace_listings_created_by_user_id", "marketplace_listings", ["created_by_user_id"]
    )
    op.create_index("ix_marketplace_listings_condition", "marketplace_listings", ["condition"])
    op.create_index("ix_marketplace_listings_status", "marketplace_listings", ["status"])
    op.create_index(
        "ix_marketplace_listings_moderation_status", "marketplace_listings", ["moderation_status"]
    )

    op.create_table(
        "marketplace_listing_media",
        sa.Column(
            "listing_id",
            sa.Uuid(),
            sa.ForeignKey("marketplace_listings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "media_asset_id",
            sa.Uuid(),
            sa.ForeignKey("media_assets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.UniqueConstraint("listing_id", "position", name="uq_marketplace_listing_media_position"),
        sa.UniqueConstraint(
            "listing_id", "media_asset_id", name="uq_marketplace_listing_media_asset"
        ),
    )
    op.create_index(
        "ix_marketplace_listing_media_listing_id", "marketplace_listing_media", ["listing_id"]
    )
    op.create_index(
        "ix_marketplace_listing_media_media_asset_id",
        "marketplace_listing_media",
        ["media_asset_id"],
    )

    op.create_table(
        "marketplace_shipping_allowances",
        sa.Column(
            "scope",
            postgresql.ENUM(name="shipping_allowance_scope", create_type=False),
            nullable=False,
        ),
        sa.Column("destination_code", sa.String(16), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("allowed_shipping_minor", sa.Integer(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        *_timestamps(),
        sa.CheckConstraint("allowed_shipping_minor >= 0", name="ck_shipping_allowance_nonnegative"),
        sa.UniqueConstraint(
            "scope",
            "destination_code",
            "currency",
            name="uq_shipping_allowance_destination_currency",
        ),
    )
    op.create_index(
        "ix_marketplace_shipping_allowances_scope", "marketplace_shipping_allowances", ["scope"]
    )
    op.create_index(
        "ix_marketplace_shipping_allowances_destination_code",
        "marketplace_shipping_allowances",
        ["destination_code"],
    )

    op.create_table(
        "marketplace_orders",
        sa.Column("public_id", sa.String(48), nullable=False, unique=True),
        sa.Column(
            "listing_id",
            sa.Uuid(),
            sa.ForeignKey("marketplace_listings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
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
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("destination_country_code", sa.String(2), nullable=False),
        sa.Column("item_subtotal_minor", sa.Integer(), nullable=False),
        sa.Column("shipping_charged_minor", sa.Integer(), nullable=False),
        sa.Column("shipping_allowance_minor", sa.Integer(), nullable=False),
        sa.Column("shipping_pass_through_minor", sa.Integer(), nullable=False),
        sa.Column("shipping_excess_minor", sa.Integer(), nullable=False),
        sa.Column("commissionable_base_minor", sa.Integer(), nullable=False),
        sa.Column("platform_fee_minor", sa.Integer(), nullable=False),
        sa.Column("creator_amount_minor", sa.Integer(), nullable=False),
        sa.Column("group_amount_minor", sa.Integer(), nullable=False),
        sa.Column("total_paid_minor", sa.Integer(), nullable=False),
        sa.Column("commission_basis_points", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="marketplace_order_status", create_type=False),
            nullable=False,
            server_default="awaiting_payment",
        ),
        sa.Column("reservation_expires_at", sa.DateTime(timezone=True)),
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
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint("quantity > 0", name="ck_marketplace_order_positive_quantity"),
        sa.CheckConstraint(
            "item_subtotal_minor > 0", name="ck_marketplace_order_positive_subtotal"
        ),
        sa.CheckConstraint(
            "shipping_charged_minor >= 0", name="ck_marketplace_order_nonnegative_shipping"
        ),
        sa.CheckConstraint(
            "shipping_allowance_minor >= 0", name="ck_marketplace_order_nonnegative_allowance"
        ),
        sa.CheckConstraint(
            "shipping_pass_through_minor >= 0", name="ck_marketplace_order_nonnegative_pass_through"
        ),
        sa.CheckConstraint(
            "shipping_excess_minor >= 0", name="ck_marketplace_order_nonnegative_excess"
        ),
        sa.CheckConstraint(
            "commissionable_base_minor > 0",
            name="ck_marketplace_order_positive_commissionable_base",
        ),
        sa.CheckConstraint("platform_fee_minor >= 0", name="ck_marketplace_order_nonnegative_fee"),
        sa.CheckConstraint(
            "creator_amount_minor >= 0", name="ck_marketplace_order_nonnegative_creator"
        ),
        sa.CheckConstraint(
            "group_amount_minor >= 0", name="ck_marketplace_order_nonnegative_group"
        ),
        sa.CheckConstraint("total_paid_minor > 0", name="ck_marketplace_order_positive_total"),
        sa.CheckConstraint(
            "shipping_pass_through_minor + shipping_excess_minor = shipping_charged_minor",
            name="ck_marketplace_order_shipping_balance",
        ),
        sa.CheckConstraint(
            "commissionable_base_minor = item_subtotal_minor + shipping_excess_minor",
            name="ck_marketplace_order_commissionable_base",
        ),
        sa.CheckConstraint(
            "total_paid_minor = item_subtotal_minor + shipping_charged_minor",
            name="ck_marketplace_order_total_paid",
        ),
        sa.CheckConstraint(
            "commissionable_base_minor = platform_fee_minor + creator_amount_minor + group_amount_minor",
            name="ck_marketplace_order_commissionable_balance",
        ),
    )
    for column in ("public_id", "listing_id", "buyer_user_id", "seller_creator_id", "status"):
        op.create_index(
            f"ix_marketplace_orders_{column}",
            "marketplace_orders",
            [column],
            unique=column == "public_id",
        )


def downgrade() -> None:
    op.drop_table("marketplace_orders")
    op.drop_table("marketplace_shipping_allowances")
    op.drop_table("marketplace_listing_media")
    op.drop_table("marketplace_listings")
    # PostgreSQL enum additions remain so deployed financial history stays readable.
    for name in (
        "marketplace_order_status",
        "shipping_allowance_scope",
        "marketplace_shipping_mode",
        "marketplace_condition",
        "marketplace_listing_status",
    ):
        op.execute(f"DROP TYPE {name}")
