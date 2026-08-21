"""Add group-owned ledger accounts for immutable Phase 8 allocations."""

import sqlalchemy as sa

from alembic import op

revision = "20260821_0013"
down_revision = "20260821_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE ledger_account_kind ADD VALUE IF NOT EXISTS 'group_pending'")
    op.execute("ALTER TYPE ledger_account_kind ADD VALUE IF NOT EXISTS 'group_available'")
    op.add_column("ledger_accounts", sa.Column("owner_group_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_ledger_accounts_owner_group", "ledger_accounts", "groups", ["owner_group_id"], ["id"], ondelete="RESTRICT"
    )
    op.create_index("ix_ledger_accounts_owner_group_id", "ledger_accounts", ["owner_group_id"])
    op.create_index(
        "uq_ledger_group_account_kind_currency", "ledger_accounts", ["owner_group_id", "kind", "currency"], unique=True,
        postgresql_where=sa.text("owner_group_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_ledger_group_account_kind_currency", table_name="ledger_accounts")
    op.drop_index("ix_ledger_accounts_owner_group_id", table_name="ledger_accounts")
    op.drop_constraint("fk_ledger_accounts_owner_group", "ledger_accounts", type_="foreignkey")
    op.drop_column("ledger_accounts", "owner_group_id")
    # PostgreSQL enum values are intentionally retained: deployed ledger history must remain readable.
