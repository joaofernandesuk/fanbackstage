"""Expand the shared Live tip and gift catalogues.

Revision ID: 20260903_0051
Revises: 20260903_0050
Create Date: 2026-09-03
"""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260903_0051"
down_revision = "20260903_0050"
branch_labels = None
depends_on = None

TIP_ROWS = [
    ("10000000-0000-4000-8000-000000000013", "Big smile", "/live/catalogue/gift-heart.svg", 200, 18),
    ("10000000-0000-4000-8000-000000000014", "Cheers", "/live/catalogue/gift-cocktail.svg", 600, 32),
    ("10000000-0000-4000-8000-000000000015", "Standing ovation", "/live/catalogue/tip-encore.svg", 2000, 48),
    ("10000000-0000-4000-8000-000000000016", "VIP moment", "/live/catalogue/gift-crown.svg", 5000, 58),
    ("10000000-0000-4000-8000-000000000017", "Superstar", "/live/catalogue/tip-legend.svg", 7500, 65),
    ("10000000-0000-4000-8000-000000000018", "Unforgettable", "/live/catalogue/gift-diamond.svg", 15000, 80),
]

GIFT_ROWS = [
    ("20000000-0000-4000-8000-000000000006", "Sweet Kiss", "/live/catalogue/gift-kiss.svg", 150, 15, "classic"),
    ("20000000-0000-4000-8000-000000000007", "Champagne", "/live/catalogue/gift-champagne.svg", 750, 25, "classic"),
    ("20000000-0000-4000-8000-000000000008", "Teddy Bear", "/live/catalogue/gift-teddy.svg", 1500, 35, "premium"),
    ("20000000-0000-4000-8000-000000000009", "Sports Car", "/live/catalogue/gift-car.svg", 7500, 60, "luxury"),
    ("20000000-0000-4000-8000-000000000010", "Private Jet", "/live/catalogue/gift-jet.svg", 15000, 70, "luxury"),
]


def _upsert(table_name: str, rows: list[dict], columns: list[str]) -> None:
    table = sa.table(table_name, *(sa.column(column) for column in columns))
    insert = postgresql.insert(table).values(rows)
    op.get_bind().execute(
        insert.on_conflict_do_update(
            index_elements=["id"],
            set_={column: getattr(insert.excluded, column) for column in columns if column != "id"},
        )
    )


def upgrade() -> None:
    _upsert("live_tip_catalog_items", [
        {"id": UUID(item_id), "label": label, "icon": icon, "amount_minor": amount,
         "currency": "EUR", "active": True, "sort_order": order}
        for item_id, label, icon, amount, order in TIP_ROWS
    ], ["id", "label", "icon", "amount_minor", "currency", "active", "sort_order"])
    _upsert("live_gift_catalog_items", [
        {"id": UUID(item_id), "name": name, "icon": icon, "amount_minor": amount,
         "currency": "EUR", "active": True, "sort_order": order, "category": category}
        for item_id, name, icon, amount, order, category in GIFT_ROWS
    ], ["id", "name", "icon", "amount_minor", "currency", "active", "sort_order", "category"])


def downgrade() -> None:
    tip_ids = ", ".join(f"'{row[0]}'" for row in TIP_ROWS)
    gift_ids = ", ".join(f"'{row[0]}'" for row in GIFT_ROWS)
    op.execute(sa.text(f"UPDATE live_tip_catalog_items SET active = false WHERE id IN ({tip_ids})"))
    op.execute(sa.text(f"UPDATE live_gift_catalog_items SET active = false WHERE id IN ({gift_ids})"))
