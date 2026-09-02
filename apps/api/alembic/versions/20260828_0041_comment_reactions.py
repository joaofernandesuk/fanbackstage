"""Add first-class reactions for social comments."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260828_0041"
down_revision = "20260828_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "post_comment_reactions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "comment_id",
            sa.UUID(),
            sa.ForeignKey("post_comments.id", ondelete="CASCADE"),
            nullable=False,
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
        sa.UniqueConstraint("comment_id", "user_id", name="uq_post_comment_reactions_comment_user"),
    )
    op.create_index(
        "ix_post_comment_reactions_comment_id", "post_comment_reactions", ["comment_id"]
    )
    op.create_index("ix_post_comment_reactions_user_id", "post_comment_reactions", ["user_id"])


def downgrade() -> None:
    op.drop_table("post_comment_reactions")
