import enum
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, Timestamped, UUIDPrimaryKey

if TYPE_CHECKING:
    from app.models.creator import CreatorProfile


class TokenPurpose(str, enum.Enum):
    email_verification = "email_verification"
    password_reset = "password_reset"


class User(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("email = lower(email)", name="ck_users_email_normalized"),
        CheckConstraint(
            "(adult_attested_at IS NULL AND adult_attestation_version IS NULL) OR "
            "(adult_attested_at IS NOT NULL AND adult_attestation_version IS NOT NULL)",
            name="ck_users_adult_attestation_complete",
        ),
    )
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    adult_attested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    adult_attestation_version: Mapped[str | None] = mapped_column(String(64))
    country_code: Mapped[str | None] = mapped_column(
        ForeignKey("country_registry.code", ondelete="RESTRICT"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    roles: Mapped[list["Role"]] = relationship(secondary="user_roles", lazy="selectin")
    creator_profile: Mapped["CreatorProfile | None"] = relationship(
        back_populates="user", uselist=False
    )


class Role(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "roles"
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(String(255))


class UserRole(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "user_roles"
    __table_args__ = (UniqueConstraint("user_id", "role_id"),)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role_id: Mapped[UUID] = mapped_column(ForeignKey("roles.id", ondelete="RESTRICT"), index=True)


class UserSession(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "user_sessions"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(Text)
    ip_address: Mapped[str | None] = mapped_column(INET)


class SecurityToken(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "security_tokens"
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[TokenPurpose] = mapped_column(
        Enum(TokenPurpose, name="token_purpose"), index=True
    )
    secret_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
