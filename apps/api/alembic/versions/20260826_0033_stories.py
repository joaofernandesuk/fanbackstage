"""Add media-backed Stories with a fixed 24-hour lifecycle."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260826_0033"
down_revision = "20260826_0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    story_status = postgresql.ENUM(
        "active", "expired", "deleted", "removed", name="story_status", create_type=False
    )
    story_status.create(op.get_bind(), checkfirst=True)
    access_policy = postgresql.ENUM(
        "free",
        "followers",
        "subscription",
        "ppv",
        "private",
        name="access_policy",
        create_type=False,
    )
    op.create_table(
        "stories",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "creator_id",
            sa.UUID(),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "media_asset_id",
            sa.UUID(),
            sa.ForeignKey("media_assets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("status", story_status, server_default=sa.text("'active'"), nullable=False),
        sa.Column(
            "access_policy",
            access_policy,
            server_default=sa.text("'free'"),
            nullable=False,
        ),
        sa.Column("caption", sa.Text()),
        sa.Column("alt_text", sa.String(500)),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expired_at", sa.DateTime(timezone=True)),
        sa.Column("deleted_at", sa.DateTime(timezone=True)),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "access_policy IN ('free', 'followers', 'subscription')",
            name="ck_stories_supported_access_policy",
        ),
        sa.CheckConstraint(
            "expires_at = published_at + INTERVAL '24 hours'",
            name="ck_stories_fixed_lifecycle",
        ),
        sa.CheckConstraint(
            "(status = 'active' AND expired_at IS NULL AND deleted_at IS NULL AND removed_at IS NULL) "
            "OR (status = 'expired' AND expired_at IS NOT NULL AND deleted_at IS NULL AND removed_at IS NULL) "
            "OR (status = 'deleted' AND deleted_at IS NOT NULL AND removed_at IS NULL) "
            "OR (status = 'removed' AND removed_at IS NOT NULL AND deleted_at IS NULL)",
            name="ck_stories_status_timestamps",
        ),
        sa.UniqueConstraint("creator_id", "idempotency_key", name="uq_stories_creator_idempotency"),
    )
    for column in (
        "creator_id",
        "created_by_user_id",
        "media_asset_id",
        "status",
        "access_policy",
        "published_at",
        "expires_at",
    ):
        op.create_index(f"ix_stories_{column}", "stories", [column])
    op.create_index(
        "ix_stories_public_rail",
        "stories",
        ["status", "expires_at", "published_at", "id"],
    )
    op.create_index(
        "ix_stories_creator_status",
        "stories",
        ["creator_id", "status", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_table("stories")
    postgresql.ENUM(name="story_status").drop(op.get_bind(), checkfirst=True)
