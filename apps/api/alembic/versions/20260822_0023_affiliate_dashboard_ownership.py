"""Associate affiliate partners with their authenticated dashboard owner."""

import sqlalchemy as sa

from alembic import op

revision = "20260822_0023"
down_revision = "20260822_0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("affiliate_partners", sa.Column("owner_user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_affiliate_partners_owner_user",
        "affiliate_partners",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_affiliate_partners_owner_user_id", "affiliate_partners", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("ix_affiliate_partners_owner_user_id", table_name="affiliate_partners")
    op.drop_constraint("fk_affiliate_partners_owner_user", "affiliate_partners", type_="foreignkey")
    op.drop_column("affiliate_partners", "owner_user_id")
