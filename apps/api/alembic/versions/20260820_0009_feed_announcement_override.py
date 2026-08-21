"""Add explicit content publication feed-announcement override."""

import sqlalchemy as sa

from alembic import op

revision = "20260820_0009"
down_revision = "20260820_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("content_items", sa.Column("feed_announcement_override", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("content_items", "feed_announcement_override")
