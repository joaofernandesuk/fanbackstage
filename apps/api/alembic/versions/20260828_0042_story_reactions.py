"""Add persistent viewer reactions for active Stories."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "20260828_0042"
down_revision = "20260828_0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "story_reactions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "story_id", sa.UUID(), sa.ForeignKey("stories.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "reaction_type",
            postgresql.ENUM("like", "love", "fire", "wow", name="reaction_type", create_type=False),
            nullable=False,
            server_default="like",
        ),
        sa.UniqueConstraint("story_id", "user_id", name="uq_story_reactions_story_user"),
    )
    op.create_index("ix_story_reactions_story_id", "story_reactions", ["story_id"])
    op.create_index("ix_story_reactions_user_id", "story_reactions", ["user_id"])


def downgrade() -> None:
    op.drop_table("story_reactions")
