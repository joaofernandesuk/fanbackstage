"""Record Phase 13 featuring-eligibility restoration without resurrecting bookings.

The new action value is a forward-only audit distinction. It does not change a
terminal moderation-disabled booking, its payment, refund, or ledger history.
"""

from alembic import op

revision = "20260824_0030"
down_revision = "20260824_0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TYPE moderation_action_type ADD VALUE IF NOT EXISTS "
        "'featured_placement_eligibility_restored'"
    )


def downgrade() -> None:
    # PostgreSQL enums cannot safely remove a published value without rewriting
    # immutable action history, so downgrade deliberately preserves audit data.
    pass
