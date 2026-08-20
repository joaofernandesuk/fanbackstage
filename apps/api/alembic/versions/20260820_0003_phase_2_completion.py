import sqlalchemy as sa

from alembic import op

"""phase 2 lifecycle and media recovery"""

revision = "20260820_0003"
down_revision = "350b070c3fd5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "media_assets",
        sa.Column("processing_attempts", sa.Integer(), server_default="0", nullable=False),
    )
    op.alter_column("media_assets", "processing_attempts", server_default=None)


def downgrade() -> None:
    op.drop_column("media_assets", "processing_attempts")
