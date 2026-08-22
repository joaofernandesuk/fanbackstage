"""Store buyer fulfilment addresses in a restricted marketplace table."""

import sqlalchemy as sa

from alembic import op

revision = "20260822_0017"
down_revision = "20260821_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "marketplace_shipping_addresses",
        sa.Column(
            "order_id",
            sa.Uuid(),
            sa.ForeignKey("marketplace_orders.id", ondelete="RESTRICT"),
            nullable=False,
            unique=True,
        ),
        sa.Column("recipient_name", sa.String(160), nullable=False),
        sa.Column("line1", sa.String(160), nullable=False),
        sa.Column("line2", sa.String(160)),
        sa.Column("city", sa.String(120), nullable=False),
        sa.Column("region_code", sa.String(16)),
        sa.Column("postal_code", sa.String(32), nullable=False),
        sa.Column("country_code", sa.String(2), nullable=False),
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
    )
    op.create_index(
        "ix_marketplace_shipping_addresses_order_id",
        "marketplace_shipping_addresses",
        ["order_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("marketplace_shipping_addresses")
