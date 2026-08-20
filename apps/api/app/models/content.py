import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamped, UUIDPrimaryKey


class MediaType(str, enum.Enum):
    image = "image"
    video = "video"


class MediaStatus(str, enum.Enum):
    pending_upload = "pending_upload"
    uploaded = "uploaded"
    queued = "queued"
    processing = "processing"
    ready = "ready"
    failed = "failed"
    rejected = "rejected"
    archived = "archived"


class DerivativeType(str, enum.Enum):
    thumbnail = "thumbnail"
    display = "display"
    blurred_preview = "blurred_preview"
    poster = "poster"
    preview_clip = "preview_clip"
    playback = "playback"


class ModerationStatus(str, enum.Enum):
    not_reviewed = "not_reviewed"
    queued = "queued"
    approved = "approved"
    flagged = "flagged"
    rejected = "rejected"
    removed = "removed"


class ContentType(str, enum.Enum):
    gallery = "gallery"
    video = "video"


class ContentStatus(str, enum.Enum):
    draft = "draft"
    processing = "processing"
    pending_review = "pending_review"
    published = "published"
    archived = "archived"
    rejected = "rejected"
    removed = "removed"


class AccessPolicy(str, enum.Enum):
    free = "free"
    followers = "followers"
    subscription = "subscription"
    ppv = "ppv"
    private = "private"


class EntitlementStatus(str, enum.Enum):
    active = "active"
    revoked = "revoked"
    expired = "expired"


class MediaAsset(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "media_assets"
    owner_creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    media_type: Mapped[MediaType] = mapped_column(Enum(MediaType, name="media_type"), index=True)
    status: Mapped[MediaStatus] = mapped_column(
        Enum(MediaStatus, name="media_status"), index=True, default=MediaStatus.pending_upload
    )
    moderation_status: Mapped[ModerationStatus] = mapped_column(
        Enum(ModerationStatus, name="moderation_status"),
        default=ModerationStatus.not_reviewed,
        index=True,
    )
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    original_filename: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str] = mapped_column(String(127))
    size_bytes: Mapped[int | None] = mapped_column()
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    processing_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    processing_error: Mapped[str | None] = mapped_column(String(255))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    derivatives: Mapped[list["MediaDerivative"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class MediaDerivative(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "media_derivatives"
    __table_args__ = (UniqueConstraint("media_asset_id", "derivative_type"),)
    media_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("media_assets.id", ondelete="CASCADE"), index=True
    )
    derivative_type: Mapped[DerivativeType] = mapped_column(
        Enum(DerivativeType, name="derivative_type")
    )
    status: Mapped[MediaStatus] = mapped_column(
        Enum(MediaStatus, name="media_status", create_type=False), default=MediaStatus.queued
    )
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    mime_type: Mapped[str] = mapped_column(String(127))
    size_bytes: Mapped[int | None] = mapped_column()
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[int | None] = mapped_column(Integer)


class ContentItem(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "content_items"
    __table_args__ = (
        CheckConstraint(
            "price_amount_minor IS NULL OR (price_amount_minor > 0 AND price_currency IS NOT NULL)",
            name="ck_content_price_valid",
        ),
    )
    owner_creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    content_type: Mapped[ContentType] = mapped_column(
        Enum(ContentType, name="content_type"), index=True
    )
    status: Mapped[ContentStatus] = mapped_column(
        Enum(ContentStatus, name="content_status"), default=ContentStatus.draft, index=True
    )
    moderation_status: Mapped[ModerationStatus] = mapped_column(
        Enum(ModerationStatus, name="moderation_status", create_type=False),
        default=ModerationStatus.not_reviewed,
        index=True,
    )
    access_policy: Mapped[AccessPolicy] = mapped_column(
        Enum(AccessPolicy, name="access_policy"), default=AccessPolicy.free, index=True
    )
    price_amount_minor: Mapped[int | None] = mapped_column(Integer)
    price_currency: Mapped[str | None] = mapped_column(String(3))
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    gallery: Mapped["Gallery | None"] = relationship(
        back_populates="content", uselist=False, cascade="all, delete-orphan", lazy="selectin"
    )
    video: Mapped["VideoContent | None"] = relationship(
        back_populates="content", uselist=False, cascade="all, delete-orphan", lazy="selectin"
    )


class Gallery(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "galleries"
    content_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), unique=True
    )
    cover_media_asset_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("media_assets.id", ondelete="SET NULL")
    )
    preview_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content: Mapped[ContentItem] = relationship(back_populates="gallery")
    items: Mapped[list["GalleryItem"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="GalleryItem.position"
    )


class GalleryItem(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "gallery_items"
    __table_args__ = (
        UniqueConstraint("gallery_id", "position", name="uq_gallery_items_gallery_position"),
        UniqueConstraint("gallery_id", "media_asset_id", name="uq_gallery_items_gallery_asset"),
    )
    gallery_id: Mapped[UUID] = mapped_column(
        ForeignKey("galleries.id", ondelete="CASCADE"), index=True
    )
    media_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("media_assets.id", ondelete="RESTRICT"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)
    is_preview: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class VideoContent(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "video_contents"
    content_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), unique=True
    )
    source_media_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("media_assets.id", ondelete="RESTRICT"), unique=True
    )
    preview_start_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    preview_duration_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content: Mapped[ContentItem] = relationship(back_populates="video")


class ContentPerformer(Base):
    __tablename__ = "content_performers"
    content_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), primary_key=True
    )
    creator_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), primary_key=True
    )


class ContentEntitlement(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "content_entitlements"
    subject_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    content_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_items.id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(64))
    source_reference: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[EntitlementStatus] = mapped_column(
        Enum(EntitlementStatus, name="entitlement_status"),
        default=EntitlementStatus.active,
        index=True,
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
