"""Add admin-configurable shipping allowance precedence fields.

No paid marketplace order is updated by this migration: allowance treatment is
already frozen directly on the order and ledger transaction at checkout.
"""

import sqlalchemy as sa

from alembic import op

revision = "20260821_0015"
down_revision = "20260821_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE shipping_allowance_scope ADD VALUE IF NOT EXISTS 'country_region'")
    op.execute("ALTER TYPE shipping_allowance_scope ADD VALUE IF NOT EXISTS 'global'")
    op.add_column(
        "marketplace_shipping_allowances", sa.Column("country_code", sa.String(2), nullable=True)
    )
    op.add_column(
        "marketplace_shipping_allowances", sa.Column("region_code", sa.String(16), nullable=True)
    )
    op.create_index(
        "ix_marketplace_shipping_allowances_country_code",
        "marketplace_shipping_allowances",
        ["country_code"],
    )
    op.create_index(
        "ix_marketplace_shipping_allowances_region_code",
        "marketplace_shipping_allowances",
        ["region_code"],
    )
    # Preserve Phase 9's first country rows while introducing explicit fields.
    op.execute(
        "UPDATE marketplace_shipping_allowances "
        "SET country_code = destination_code "
        "WHERE scope = 'country' AND country_code IS NULL"
    )


def downgrade() -> None:
    op.drop_index(
        "ix_marketplace_shipping_allowances_region_code",
        table_name="marketplace_shipping_allowances",
    )
    op.drop_index(
        "ix_marketplace_shipping_allowances_country_code",
        table_name="marketplace_shipping_allowances",
    )
    op.drop_column("marketplace_shipping_allowances", "region_code")
    op.drop_column("marketplace_shipping_allowances", "country_code")
    # Enum additions remain for safe downgrade compatibility with audit history.
