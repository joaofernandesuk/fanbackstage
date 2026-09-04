"""Add the platform-owned Live tip catalogue and seed shared commerce choices.

Revision ID: 20260903_0049
Revises: 20260903_0048
Create Date: 2026-09-03

The legacy creator tip-menu table remains intact because completed charges may
reference it. New tips reference the shared catalogue and snapshot their value
in the immutable Live commerce charge.
"""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260903_0049"
down_revision = "20260903_0048"
branch_labels = None
depends_on = None


TIP_ROWS = [
    (
        "10000000-0000-4000-8000-000000000001",
        "A little love",
        "/live/catalogue/tip-love.svg",
        100,
        10,
    ),
    (
        "10000000-0000-4000-8000-000000000002",
        "You look amazing",
        "/live/catalogue/tip-sparkle.svg",
        250,
        20,
    ),
    (
        "10000000-0000-4000-8000-000000000003",
        "Keep it going",
        "/live/catalogue/tip-flame.svg",
        500,
        30,
    ),
    (
        "10000000-0000-4000-8000-000000000004",
        "Showstopper",
        "/live/catalogue/tip-star.svg",
        1000,
        40,
    ),
    (
        "10000000-0000-4000-8000-000000000005",
        "Super fan",
        "/live/catalogue/tip-crown.svg",
        2500,
        50,
    ),
    (
        "10000000-0000-4000-8000-000000000006",
        "Headliner",
        "/live/catalogue/tip-diamond.svg",
        5000,
        60,
    ),
]

GIFT_ROWS = [
    (
        "20000000-0000-4000-8000-000000000001",
        "Red Rose",
        "/live/catalogue/gift-rose.svg",
        300,
        10,
        "classic",
    ),
    (
        "20000000-0000-4000-8000-000000000002",
        "Cocktail",
        "/live/catalogue/gift-cocktail.svg",
        500,
        20,
        "classic",
    ),
    (
        "20000000-0000-4000-8000-000000000003",
        "Golden Heart",
        "/live/catalogue/gift-heart.svg",
        1000,
        30,
        "premium",
    ),
    (
        "20000000-0000-4000-8000-000000000004",
        "Royal Crown",
        "/live/catalogue/gift-crown.svg",
        2500,
        40,
        "premium",
    ),
    (
        "20000000-0000-4000-8000-000000000005",
        "Blue Diamond",
        "/live/catalogue/gift-diamond.svg",
        5000,
        50,
        "premium",
    ),
]


def upgrade() -> None:
    op.create_table(
        "live_tip_catalog_items",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("label", sa.String(100), nullable=False),
        sa.Column("icon", sa.String(160), nullable=False),
        sa.Column("amount_minor", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("amount_minor > 0", name="ck_live_tip_catalog_amount_positive"),
        sa.CheckConstraint("btrim(label) <> ''", name="ck_live_tip_catalog_label"),
        sa.UniqueConstraint("label", "currency", name="uq_live_tip_catalog_label_currency"),
    )
    op.create_index("ix_live_tip_catalog_items_active", "live_tip_catalog_items", ["active"])
    op.add_column(
        "live_commerce_charges", sa.Column("tip_catalog_item_id", sa.UUID(), nullable=True)
    )
    op.create_foreign_key(
        "fk_live_commerce_charges_tip_catalog_item_id",
        "live_commerce_charges",
        "live_tip_catalog_items",
        ["tip_catalog_item_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    tip_table = sa.table(
        "live_tip_catalog_items",
        sa.column("id", sa.UUID()),
        sa.column("label", sa.String()),
        sa.column("icon", sa.String()),
        sa.column("amount_minor", sa.Integer()),
        sa.column("currency", sa.String()),
        sa.column("active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
    )
    op.bulk_insert(
        tip_table,
        [
            {
                "id": UUID(item_id),
                "label": label,
                "icon": icon,
                "amount_minor": amount,
                "currency": "EUR",
                "active": True,
                "sort_order": order,
            }
            for item_id, label, icon, amount, order in TIP_ROWS
        ],
    )

    gift_table = sa.table(
        "live_gift_catalog_items",
        sa.column("id", sa.UUID()),
        sa.column("name", sa.String()),
        sa.column("icon", sa.String()),
        sa.column("amount_minor", sa.Integer()),
        sa.column("currency", sa.String()),
        sa.column("active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
        sa.column("category", sa.String()),
    )
    op.get_bind().execute(
        postgresql.insert(gift_table)
        .values(
            [
                {
                    "id": UUID(item_id),
                    "name": name,
                    "icon": icon,
                    "amount_minor": amount,
                    "currency": "EUR",
                    "active": True,
                    "sort_order": order,
                    "category": category,
                }
                for item_id, name, icon, amount, order, category in GIFT_ROWS
            ]
        )
        .on_conflict_do_nothing(index_elements=["name"])
    )


def downgrade() -> None:
    raise RuntimeError(
        "Live catalogue migration 20260903_0049 is forward-only because financial charges may reference catalogue rows"
    )
