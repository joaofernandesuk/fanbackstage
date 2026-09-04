"""Expand the shared platform Live tip catalogue.

Revision ID: 20260903_0050
Revises: 20260903_0049
Create Date: 2026-09-03
"""

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260903_0050"
down_revision = "20260903_0049"
branch_labels = None
depends_on = None


TIP_ROWS = [
    ("10000000-0000-4000-8000-000000000007", "Sweet kiss", "/live/catalogue/tip-kiss.svg", 150, 15),
    (
        "10000000-0000-4000-8000-000000000008",
        "Coffee break",
        "/live/catalogue/tip-coffee.svg",
        350,
        25,
    ),
    ("10000000-0000-4000-8000-000000000009", "Encore", "/live/catalogue/tip-encore.svg", 750, 35),
    (
        "10000000-0000-4000-8000-000000000010",
        "Spotlight",
        "/live/catalogue/tip-spotlight.svg",
        1500,
        45,
    ),
    (
        "10000000-0000-4000-8000-000000000011",
        "Fireworks",
        "/live/catalogue/tip-fireworks.svg",
        3500,
        55,
    ),
    ("10000000-0000-4000-8000-000000000012", "Legend", "/live/catalogue/tip-legend.svg", 10000, 70),
]


def upgrade() -> None:
    tips = sa.table(
        "live_tip_catalog_items",
        sa.column("id", sa.UUID()),
        sa.column("label", sa.String()),
        sa.column("icon", sa.String()),
        sa.column("amount_minor", sa.Integer()),
        sa.column("currency", sa.String()),
        sa.column("active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
    )
    rows = [
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
    ]
    insert = postgresql.insert(tips).values(rows)
    op.get_bind().execute(
        insert.on_conflict_do_update(
            index_elements=["id"],
            set_={
                "label": insert.excluded.label,
                "icon": insert.excluded.icon,
                "amount_minor": insert.excluded.amount_minor,
                "currency": insert.excluded.currency,
                "active": insert.excluded.active,
                "sort_order": insert.excluded.sort_order,
            },
        )
    )


def downgrade() -> None:
    # Preserve any immutable charge references; removing availability is safe.
    op.execute(
        sa.text(
            "UPDATE live_tip_catalog_items SET active = false "
            "WHERE id IN ('10000000-0000-4000-8000-000000000007', "
            "'10000000-0000-4000-8000-000000000008', "
            "'10000000-0000-4000-8000-000000000009', "
            "'10000000-0000-4000-8000-000000000010', "
            "'10000000-0000-4000-8000-000000000011', "
            "'10000000-0000-4000-8000-000000000012')"
        )
    )
