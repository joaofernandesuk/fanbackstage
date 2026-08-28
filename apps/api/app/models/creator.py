import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamped, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.identity import User


class CreatorStatus(str, enum.Enum):
    draft = "draft"
    pending_verification = "pending_verification"
    pending_review = "pending_review"
    approved = "approved"
    rejected = "rejected"
    suspended = "suspended"
    disabled = "disabled"


class VerificationStatus(str, enum.Enum):
    not_started = "not_started"
    pending = "pending"
    verified = "verified"
    failed = "failed"
    expired = "expired"
    needs_review = "needs_review"
    revoked = "revoked"
    suspended = "suspended"


class CreatorProfile(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "creator_profiles"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    username: Mapped[str | None] = mapped_column(String(32), unique=True)
    display_name: Mapped[str | None] = mapped_column(String(80))
    bio: Mapped[str | None] = mapped_column(Text)
    avatar_reference: Mapped[str | None] = mapped_column(String(255))
    cover_reference: Mapped[str | None] = mapped_column(String(255))
    country_code: Mapped[str | None] = mapped_column(String(2))
    region: Mapped[str | None] = mapped_column(String(80))
    city: Mapped[str | None] = mapped_column(String(80))
    show_location: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    timezone: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[CreatorStatus] = mapped_column(
        Enum(CreatorStatus, name="creator_status"), default=CreatorStatus.draft, index=True
    )
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(255))
    user: Mapped["User"] = relationship(back_populates="creator_profile")
    categories: Mapped[list["CreatorCategory"]] = relationship(
        secondary="creator_profile_categories", lazy="selectin"
    )
    languages: Mapped[list["CreatorLanguage"]] = relationship(
        secondary="creator_profile_languages", lazy="selectin"
    )
    links: Mapped[list["CreatorSocialLink"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class CreatorVerification(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "creator_verifications"
    creator_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(64))
    provider_reference: Mapped[str] = mapped_column(String(255), unique=True)
    status: Mapped[VerificationStatus] = mapped_column(
        Enum(VerificationStatus, name="verification_status"), index=True
    )
    adult_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    identity_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    country_code: Mapped[str | None] = mapped_column(
        ForeignKey("country_registry.code", ondelete="RESTRICT"), index=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason_code: Mapped[str | None] = mapped_column(String(96))
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class CreatorStatusHistory(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "creator_status_history"
    creator_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="CASCADE"), index=True
    )
    previous_status: Mapped[CreatorStatus | None] = mapped_column(
        Enum(CreatorStatus, name="creator_status", create_type=False)
    )
    new_status: Mapped[CreatorStatus] = mapped_column(
        Enum(CreatorStatus, name="creator_status", create_type=False)
    )
    actor_user_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reason: Mapped[str | None] = mapped_column(String(255))


class CreatorUsernameHistory(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "creator_username_history"
    username: Mapped[str] = mapped_column(String(32), unique=True)
    creator_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="CASCADE"), index=True
    )


class CreatorCategory(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "creator_categories"
    slug: Mapped[str] = mapped_column(String(48), unique=True)
    label: Mapped[str] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class CreatorLanguage(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "creator_languages"
    code: Mapped[str] = mapped_column(String(10), unique=True)
    label: Mapped[str] = mapped_column(String(80))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class CreatorProfileCategory(Base):
    __tablename__ = "creator_profile_categories"
    creator_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    category_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_categories.id", ondelete="RESTRICT"), primary_key=True
    )


class CreatorProfileLanguage(Base):
    __tablename__ = "creator_profile_languages"
    creator_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="CASCADE"), primary_key=True
    )
    language_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_languages.id", ondelete="RESTRICT"), primary_key=True
    )


class CreatorSocialLink(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "creator_social_links"
    creator_profile_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="CASCADE"), index=True
    )
    label: Mapped[str] = mapped_column(String(48))
    url: Mapped[str] = mapped_column(String(512))
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    __table_args__ = (UniqueConstraint("creator_profile_id", "url"),)
