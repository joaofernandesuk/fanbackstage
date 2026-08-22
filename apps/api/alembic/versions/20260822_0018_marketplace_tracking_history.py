"""Add immutable marketplace shipment tracking history."""

import sqlalchemy as sa

from alembic import op

revision = "20260822_0018"
down_revision = "20260822_0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("marketplace_orders", sa.Column("carrier", sa.String(120)))
    op.create_table(
        "marketplace_tracking_events",
        sa.Column(
            "order_id",
            sa.Uuid(),
            sa.ForeignKey("marketplace_orders.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("carrier", sa.String(120)),
        sa.Column("tracking_reference", sa.String(255)),
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
        "ix_marketplace_tracking_events_order_id", "marketplace_tracking_events", ["order_id"]
    )


def downgrade() -> None:
    op.drop_table("marketplace_tracking_events")
    op.drop_column("marketplace_orders", "carrier")
