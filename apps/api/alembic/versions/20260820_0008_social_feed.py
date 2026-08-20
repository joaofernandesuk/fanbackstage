"""Phase 5 social feed foundation."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ENUM

revision = "20260820_0008"
down_revision = "20260820_0007"
branch_labels = None
depends_on = None


def timestamps():
    return [sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False)]


def upgrade() -> None:
    post_type = sa.Enum("text", "image", "video", "gallery_reference", "video_reference", "mixed_media", name="feed_post_type")
    post_status = sa.Enum("draft", "scheduled", "published", "archived", "removed", name="feed_post_status")
    reaction = sa.Enum("like", "love", "fire", "wow", name="reaction_type")
    report_status = sa.Enum("open", "reviewed", "dismissed", name="social_report_status")
    op.create_table("follows", sa.Column("id", sa.Uuid(), primary_key=True), *timestamps(), sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("creator_id", sa.Uuid(), sa.ForeignKey("creator_profiles.id", ondelete="CASCADE"), nullable=False), sa.UniqueConstraint("user_id", "creator_id", name="uq_follows_user_creator"))
    op.create_index("ix_follows_user_id", "follows", ["user_id"]); op.create_index("ix_follows_creator_id", "follows", ["creator_id"])
    op.create_table("creator_feed_settings", sa.Column("id", sa.Uuid(), primary_key=True), *timestamps(), sa.Column("creator_id", sa.Uuid(), sa.ForeignKey("creator_profiles.id", ondelete="CASCADE"), nullable=False, unique=True), sa.Column("auto_post_galleries", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("auto_post_videos", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("default_comments_enabled", sa.Boolean(), nullable=False, server_default=sa.true()))
    policy = ENUM("free", "followers", "subscription", "ppv", "private", name="access_policy", create_type=False)
    moderation = ENUM("not_reviewed", "queued", "approved", "flagged", "rejected", "removed", name="moderation_status", create_type=False)
    op.create_table("feed_posts", sa.Column("id", sa.Uuid(), primary_key=True), *timestamps(), sa.Column("creator_id", sa.Uuid(), sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"), nullable=False), sa.Column("created_by_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False), sa.Column("post_type", post_type, nullable=False), sa.Column("body", sa.Text()), sa.Column("status", post_status, nullable=False), sa.Column("access_policy", policy, nullable=False), sa.Column("moderation_status", moderation, nullable=False), sa.Column("comments_enabled", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("reactions_enabled", sa.Boolean(), nullable=False, server_default=sa.true()), sa.Column("scheduled_at", sa.DateTime(timezone=True)), sa.Column("published_at", sa.DateTime(timezone=True)), sa.Column("pinned_at", sa.DateTime(timezone=True)), sa.Column("source_content_id", sa.Uuid(), sa.ForeignKey("content_items.id", ondelete="RESTRICT"), unique=True), sa.CheckConstraint("scheduled_at IS NULL OR status = 'scheduled'", name="ck_feed_post_schedule_status"))
    for col in ("creator_id", "created_by_user_id", "post_type", "status", "access_policy", "moderation_status", "scheduled_at", "published_at", "pinned_at"): op.create_index(f"ix_feed_posts_{col}", "feed_posts", [col])
    op.create_index("ix_feed_posts_creator_status_published", "feed_posts", ["creator_id", "status", "published_at", "id"]); op.create_index("ix_feed_posts_discover", "feed_posts", ["status", "published_at", "id"])
    op.create_table("feed_post_media", sa.Column("id", sa.Uuid(), primary_key=True), *timestamps(), sa.Column("post_id", sa.Uuid(), sa.ForeignKey("feed_posts.id", ondelete="CASCADE"), nullable=False), sa.Column("media_asset_id", sa.Uuid(), sa.ForeignKey("media_assets.id", ondelete="RESTRICT"), nullable=False), sa.Column("position", sa.Integer(), nullable=False), sa.Column("alt_text", sa.String(500)), sa.UniqueConstraint("post_id", "position", name="uq_feed_post_media_position"), sa.UniqueConstraint("post_id", "media_asset_id", name="uq_feed_post_media_asset"))
    op.create_index("ix_feed_post_media_post_id", "feed_post_media", ["post_id"]); op.create_index("ix_feed_post_media_media_asset_id", "feed_post_media", ["media_asset_id"])
    op.create_table("post_reactions", sa.Column("id", sa.Uuid(), primary_key=True), *timestamps(), sa.Column("post_id", sa.Uuid(), sa.ForeignKey("feed_posts.id", ondelete="CASCADE"), nullable=False), sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("reaction_type", reaction, nullable=False), sa.UniqueConstraint("post_id", "user_id", name="uq_post_reactions_post_user"))
    op.create_index("ix_post_reactions_post_id", "post_reactions", ["post_id"]); op.create_index("ix_post_reactions_user_id", "post_reactions", ["user_id"])
    op.create_table("post_comments", sa.Column("id", sa.Uuid(), primary_key=True), *timestamps(), sa.Column("post_id", sa.Uuid(), sa.ForeignKey("feed_posts.id", ondelete="CASCADE"), nullable=False), sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("parent_id", sa.Uuid(), sa.ForeignKey("post_comments.id", ondelete="CASCADE")), sa.Column("body", sa.Text(), nullable=False), sa.Column("hidden_at", sa.DateTime(timezone=True)), sa.Column("deleted_at", sa.DateTime(timezone=True)), sa.CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_post_comment_not_self"))
    for col in ("post_id", "user_id", "parent_id"): op.create_index(f"ix_post_comments_{col}", "post_comments", [col])
    op.create_table("post_mentions", sa.Column("id", sa.Uuid(), primary_key=True), *timestamps(), sa.Column("post_id", sa.Uuid(), sa.ForeignKey("feed_posts.id", ondelete="CASCADE"), nullable=False), sa.Column("mentioned_creator_id", sa.Uuid(), sa.ForeignKey("creator_profiles.id", ondelete="CASCADE"), nullable=False), sa.UniqueConstraint("post_id", "mentioned_creator_id", name="uq_post_mentions"))
    op.create_index("ix_post_mentions_post_id", "post_mentions", ["post_id"]); op.create_index("ix_post_mentions_mentioned_creator_id", "post_mentions", ["mentioned_creator_id"])
    op.create_table("hashtags", sa.Column("id", sa.Uuid(), primary_key=True), *timestamps(), sa.Column("normalized", sa.String(80), nullable=False, unique=True))
    op.create_table("post_hashtags", sa.Column("post_id", sa.Uuid(), sa.ForeignKey("feed_posts.id", ondelete="CASCADE"), primary_key=True), sa.Column("hashtag_id", sa.Uuid(), sa.ForeignKey("hashtags.id", ondelete="CASCADE"), primary_key=True))
    op.create_table("social_reports", sa.Column("id", sa.Uuid(), primary_key=True), *timestamps(), sa.Column("reporter_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False), sa.Column("target_type", sa.String(32), nullable=False), sa.Column("target_id", sa.Uuid(), nullable=False), sa.Column("reason", sa.String(80), nullable=False), sa.Column("details", sa.Text()), sa.Column("status", report_status, nullable=False), sa.UniqueConstraint("reporter_user_id", "target_type", "target_id", "reason", name="uq_social_report_dedupe"))
    for col in ("reporter_user_id", "target_type", "target_id", "status"): op.create_index(f"ix_social_reports_{col}", "social_reports", [col])


def downgrade() -> None:
    for table in ("social_reports", "post_hashtags", "hashtags", "post_mentions", "post_comments", "post_reactions", "feed_post_media", "feed_posts", "creator_feed_settings", "follows"): op.drop_table(table)
    for name in ("social_report_status", "reaction_type", "feed_post_status", "feed_post_type"): op.execute(f"DROP TYPE IF EXISTS {name}")
