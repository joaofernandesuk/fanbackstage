"""Add the chargeback ledger transaction type.

Revision ID: 20260820_0005
Revises: 20260820_0004
Create Date: 2026-08-20

PostgreSQL enum values cannot be removed safely.  Downgrade is intentionally a
no-op: production rollbacks remain forward-only for financial data, while the
preceding migration can still remove the enum when rolling back an empty local
database.
"""

from alembic import op

revision = "20260820_0005"
down_revision = "20260820_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE ledger_transaction_type ADD VALUE IF NOT EXISTS 'chargeback'")


def downgrade() -> None:
    # PostgreSQL does not support removing enum values without rewriting data.
    pass
