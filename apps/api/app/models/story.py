import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamped, UUIDPrimaryKey
from app.models.content import AccessPolicy
from app.models.social import ReactionType


class StoryStatus(str, enum.Enum):
    active = "active"
    expired = "expired"
    deleted = "deleted"
    removed = "removed"


class Story(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "stories"
    __table_args__ = (
        CheckConstraint(
            "access_policy IN ('free', 'followers', 'subscription')",
            name="ck_stories_supported_access_policy",
        ),
        CheckConstraint(
            "expires_at = published_at + INTERVAL '24 hours'",
            name="ck_stories_fixed_lifecycle",
        ),
        CheckConstraint(
            "(status = 'active' AND expired_at IS NULL AND deleted_at IS NULL AND removed_at IS NULL) "
            "OR (status = 'expired' AND expired_at IS NOT NULL AND deleted_at IS NULL AND removed_at IS NULL) "
            "OR (status = 'deleted' AND deleted_at IS NOT NULL AND removed_at IS NULL) "
            "OR (status = 'removed' AND removed_at IS NOT NULL AND deleted_at IS NULL)",
            name="ck_stories_status_timestamps",
        ),
        UniqueConstraint("creator_id", "idempotency_key", name="uq_stories_creator_idempotency"),
        Index("ix_stories_public_rail", "status", "expires_at", "published_at", "id"),
        Index("ix_stories_creator_status", "creator_id", "status", "created_at", "id"),
    )

    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    media_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("media_assets.id", ondelete="RESTRICT"), index=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(128))
    status: Mapped[StoryStatus] = mapped_column(
        Enum(StoryStatus, name="story_status"), default=StoryStatus.active, index=True
    )
    access_policy: Mapped[AccessPolicy] = mapped_column(
        Enum(AccessPolicy, name="access_policy", create_type=False),
        default=AccessPolicy.free,
        index=True,
    )
    caption: Mapped[str | None] = mapped_column(Text)
    alt_text: Mapped[str | None] = mapped_column(String(500))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    expired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StoryReaction(UUIDPrimaryKey, Timestamped, Base):
    """One current reaction per viewer for an active Story."""

    __tablename__ = "story_reactions"
    __table_args__ = (
        UniqueConstraint("story_id", "user_id", name="uq_story_reactions_story_user"),
    )

    story_id: Mapped[UUID] = mapped_column(ForeignKey("stories.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    reaction_type: Mapped[ReactionType] = mapped_column(
        Enum(ReactionType, name="reaction_type", create_type=False), default=ReactionType.like
    )
