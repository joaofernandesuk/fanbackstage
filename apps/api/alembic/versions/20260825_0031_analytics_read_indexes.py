"""Add bounded time-range indexes for Phase 14 analytics reads.

The analytics projections filter immutable ledger transactions and discovery
events by their canonical event timestamps.  These indexes do not change any
financial or attribution semantics; they only bound the read side.
"""

from alembic import op

revision = "20260825_0031"
down_revision = "20260824_0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_ledger_transactions_effective_at", "ledger_transactions", ["effective_at"])
    op.create_index("ix_discovery_events_created_at", "discovery_events", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_discovery_events_created_at", table_name="discovery_events")
    op.drop_index("ix_ledger_transactions_effective_at", table_name="ledger_transactions")
