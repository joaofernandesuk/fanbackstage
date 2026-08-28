import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamped, UUIDPrimaryKey
from app.models.content import AccessPolicy, ModerationStatus


class FeedPostType(str, enum.Enum):
    text = "text"
    image = "image"
    video = "video"
    gallery_reference = "gallery_reference"
    video_reference = "video_reference"
    mixed_media = "mixed_media"


class FeedPostStatus(str, enum.Enum):
    draft = "draft"
    scheduled = "scheduled"
    published = "published"
    archived = "archived"
    removed = "removed"


class ReactionType(str, enum.Enum):
    like = "like"
    love = "love"
    fire = "fire"
    wow = "wow"


class ReportStatus(str, enum.Enum):
    open = "open"
    reviewed = "reviewed"
    dismissed = "dismissed"


class Follow(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "follows"
    __table_args__ = (UniqueConstraint("user_id", "creator_id", name="uq_follows_user_creator"),)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="CASCADE"), index=True
    )


class CreatorFeedSettings(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "creator_feed_settings"
    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="CASCADE"), unique=True
    )
    auto_post_galleries: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_post_videos: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_comments_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class FeedPost(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "feed_posts"
    __table_args__ = (
        CheckConstraint(
            "scheduled_at IS NULL OR status = 'scheduled'", name="ck_feed_post_schedule_status"
        ),
        Index(
            "ix_feed_posts_creator_status_published", "creator_id", "status", "published_at", "id"
        ),
        Index("ix_feed_posts_discover", "status", "published_at", "id"),
    )
    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    post_type: Mapped[FeedPostType] = mapped_column(
        Enum(FeedPostType, name="feed_post_type"), index=True
    )
    body: Mapped[str | None] = mapped_column(Text)
    status: Mapped[FeedPostStatus] = mapped_column(
        Enum(FeedPostStatus, name="feed_post_status"), default=FeedPostStatus.draft, index=True
    )
    access_policy: Mapped[AccessPolicy] = mapped_column(
        Enum(AccessPolicy, name="access_policy", create_type=False),
        default=AccessPolicy.free,
        index=True,
    )
    moderation_status: Mapped[ModerationStatus] = mapped_column(
        Enum(ModerationStatus, name="moderation_status", create_type=False),
        default=ModerationStatus.not_reviewed,
        index=True,
    )
    comments_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    reactions_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    source_content_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("content_items.id", ondelete="RESTRICT"), unique=True
    )


class FeedPostMedia(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "feed_post_media"
    __table_args__ = (
        UniqueConstraint("post_id", "position", name="uq_feed_post_media_position"),
        UniqueConstraint("post_id", "media_asset_id", name="uq_feed_post_media_asset"),
    )
    post_id: Mapped[UUID] = mapped_column(
        ForeignKey("feed_posts.id", ondelete="CASCADE"), index=True
    )
    media_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("media_assets.id", ondelete="RESTRICT"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    alt_text: Mapped[str | None] = mapped_column(String(500))


class PostReaction(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "post_reactions"
    __table_args__ = (UniqueConstraint("post_id", "user_id", name="uq_post_reactions_post_user"),)
    post_id: Mapped[UUID] = mapped_column(
        ForeignKey("feed_posts.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    reaction_type: Mapped[ReactionType] = mapped_column(
        Enum(ReactionType, name="reaction_type"), default=ReactionType.like
    )


class PostComment(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "post_comments"
    __table_args__ = (
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="ck_post_comment_not_self"),
    )
    post_id: Mapped[UUID] = mapped_column(
        ForeignKey("feed_posts.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("post_comments.id", ondelete="CASCADE"), index=True
    )
    body: Mapped[str] = mapped_column(Text)
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PostMention(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "post_mentions"
    __table_args__ = (UniqueConstraint("post_id", "mentioned_creator_id", name="uq_post_mentions"),)
    post_id: Mapped[UUID] = mapped_column(
        ForeignKey("feed_posts.id", ondelete="CASCADE"), index=True
    )
    mentioned_creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="CASCADE"), index=True
    )


class Hashtag(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "hashtags"
    normalized: Mapped[str] = mapped_column(String(80), unique=True)


class PostHashtag(Base):
    __tablename__ = "post_hashtags"
    post_id: Mapped[UUID] = mapped_column(
        ForeignKey("feed_posts.id", ondelete="CASCADE"), primary_key=True
    )
    hashtag_id: Mapped[UUID] = mapped_column(
        ForeignKey("hashtags.id", ondelete="CASCADE"), primary_key=True
    )


class SocialReport(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "social_reports"
    __table_args__ = (
        UniqueConstraint(
            "reporter_user_id", "target_type", "target_id", "reason", name="uq_social_report_dedupe"
        ),
    )
    reporter_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    target_type: Mapped[str] = mapped_column(String(32), index=True)
    target_id: Mapped[UUID] = mapped_column(index=True)
    reason: Mapped[str] = mapped_column(String(80))
    details: Mapped[str | None] = mapped_column(Text)
    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus, name="social_report_status"), default=ReportStatus.open, index=True
    )
