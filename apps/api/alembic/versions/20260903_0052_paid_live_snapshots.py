"""Add creator-priced paid Live snapshots.

Revision ID: 20260903_0052
Revises: 20260903_0051
Create Date: 2026-09-03

PostgreSQL enum values cannot be removed safely; downgrade removes the settings
columns but intentionally retains the enum labels.
"""

import sqlalchemy as sa

from alembic import op

revision = "20260903_0052"
down_revision = "20260903_0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE live_commerce_kind ADD VALUE IF NOT EXISTS 'snapshot'")
    op.execute("ALTER TYPE ledger_transaction_type ADD VALUE IF NOT EXISTS 'live_snapshot'")
    op.add_column("creator_live_settings", sa.Column("snapshots_enabled", sa.Boolean(), server_default=sa.true(), nullable=False))
    op.add_column("creator_live_settings", sa.Column("snapshot_price_minor", sa.Integer(), server_default="100", nullable=False))
    op.create_check_constraint("ck_live_settings_snapshot_price", "creator_live_settings", "snapshot_price_minor > 0")


def downgrade() -> None:
    op.drop_constraint("ck_live_settings_snapshot_price", "creator_live_settings", type_="check")
    op.drop_column("creator_live_settings", "snapshot_price_minor")
    op.drop_column("creator_live_settings", "snapshots_enabled")
