"""Complete Phase 7 paid requests, reactions, rankings, and moderation enums."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260831_0047"
down_revision = "20260831_0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "live_paid_request_options",
        sa.Column(
            "requires_creator_acceptance",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )
    op.add_column(
        "live_commerce_charges",
        sa.Column(
            "creator_acceptance_required",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )
    op.execute(
        "ALTER TYPE excess_capture_source ADD VALUE IF NOT EXISTS 'live_paid_request'"
    )
    op.execute(
        "ALTER TYPE moderation_action_type ADD VALUE IF NOT EXISTS 'live_participant_remove'"
    )
    op.execute(
        "ALTER TYPE ts_report_target_type ADD VALUE IF NOT EXISTS 'live_paid_request'"
    )

    reaction_type = postgresql.ENUM(
        "love",
        "fire",
        "applause",
        "wow",
        name="live_reaction_type",
        create_type=False,
    )
    reaction_type.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "live_reaction_aggregates",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "live_room_id",
            sa.UUID(),
            sa.ForeignKey("live_rooms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reaction_type", reaction_type, nullable=False),
        sa.Column("reaction_count", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint(
            "reaction_count >= 0", name="ck_live_reaction_aggregate_nonnegative"
        ),
        sa.UniqueConstraint(
            "live_room_id",
            "reaction_type",
            name="uq_live_reaction_aggregate_room_type",
        ),
    )
    op.create_index(
        "ix_live_reaction_aggregates_live_room_id",
        "live_reaction_aggregates",
        ["live_room_id"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "Phase 7 completion includes immutable Live financial/moderation history; "
        "use a forward corrective migration"
    )
