"""Versioned legal content, exact acceptance history, and simple public site settings."""

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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamped, UUIDPrimaryKey


class LegalDocumentType(str, enum.Enum):
    terms = "terms"
    privacy = "privacy"
    cookies = "cookies"
    community_guidelines = "community_guidelines"
    acceptable_use = "acceptable_use"
    prohibited_content = "prohibited_content"
    creator_agreement = "creator_agreement"
    fan_terms = "fan_terms"
    refund_policy = "refund_policy"
    marketplace_terms = "marketplace_terms"
    live_rules = "live_rules"
    age_policy = "age_policy"
    copyright = "copyright"
    complaints = "complaints"
    appeals = "appeals"
    performer_consent = "performer_consent"
    contact_support = "contact_support"
    record_keeping_notice = "record_keeping_notice"


class LegalAudience(str, enum.Enum):
    all_users = "all_users"
    fan = "fan"
    creator = "creator"
    group_manager = "group_manager"
    affiliate = "affiliate"


class LegalDocumentStatus(str, enum.Enum):
    draft = "draft"
    published = "published"
    retired = "retired"


class LegalDocument(UUIDPrimaryKey, Timestamped, Base):
    """Stable legal-document identity; mutable copy belongs only to draft versions."""

    __tablename__ = "legal_documents"
    __table_args__ = (
        Index(
            "uq_legal_documents_global_slug_language_audience",
            "slug",
            "language",
            "audience",
            unique=True,
            postgresql_where=text("jurisdiction_code IS NULL"),
        ),
        Index(
            "uq_legal_documents_country_slug_language_audience",
            "slug",
            "jurisdiction_code",
            "language",
            "audience",
            unique=True,
            postgresql_where=text("jurisdiction_code IS NOT NULL"),
        ),
        CheckConstraint(
            "jurisdiction_code IS NULL OR jurisdiction_code ~ '^[A-Z]{2}$'",
            name="ck_legal_documents_jurisdiction_iso_alpha2",
        ),
    )

    document_type: Mapped[LegalDocumentType] = mapped_column(
        Enum(LegalDocumentType, name="legal_document_type"), index=True
    )
    slug: Mapped[str] = mapped_column(String(96), index=True)
    jurisdiction_code: Mapped[str | None] = mapped_column(
        String(2),
        ForeignKey("country_registry.code", ondelete="RESTRICT"),
        index=True,
    )
    language: Mapped[str] = mapped_column(String(16), default="en", index=True)
    audience: Mapped[LegalAudience] = mapped_column(
        Enum(LegalAudience, name="legal_audience"), index=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )


class LegalDocumentVersion(UUIDPrimaryKey, Timestamped, Base):
    """An immutable-once-published body and its explicit publication lifecycle."""

    __tablename__ = "legal_document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_legal_document_version_number"),
        CheckConstraint("version > 0", name="ck_legal_document_versions_positive_version"),
        CheckConstraint(
            "effective_until IS NULL OR effective_from IS NULL OR effective_until > effective_from",
            name="ck_legal_document_versions_effective_window",
        ),
    )

    document_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_documents.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[LegalDocumentStatus] = mapped_column(
        Enum(LegalDocumentStatus, name="legal_document_status"),
        default=LegalDocumentStatus.draft,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(200))
    body_json: Mapped[list[dict]] = mapped_column(JSONB, default=list, nullable=False)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    requires_acceptance: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, index=True
    )
    requires_legal_review: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    approved_for_publication: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    published_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    retired_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )


class LegalAcceptance(UUIDPrimaryKey, Timestamped, Base):
    """Exact user acceptance of one immutable legal-document version."""

    __tablename__ = "legal_acceptances"
    __table_args__ = (
        UniqueConstraint("user_id", "document_version_id", name="uq_legal_acceptance_user_version"),
        CheckConstraint(
            "jurisdiction_code IS NULL OR jurisdiction_code ~ '^[A-Z]{2}$'",
            name="ck_legal_acceptances_jurisdiction_iso_alpha2",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    document_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("legal_document_versions.id", ondelete="RESTRICT"), index=True
    )
    accepted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source: Mapped[str] = mapped_column(String(64))
    jurisdiction_code: Mapped[str | None] = mapped_column(
        String(2),
        ForeignKey("country_registry.code", ondelete="RESTRICT"),
        index=True,
    )
    correlation_id: Mapped[str | None] = mapped_column(String(80), index=True)


class SiteSettingsVersion(UUIDPrimaryKey, Timestamped, Base):
    """Small append-versioned public configuration, not a general page builder."""

    __tablename__ = "site_settings_versions"
    __table_args__ = (
        UniqueConstraint("version", name="uq_site_settings_version"),
        Index(
            "uq_site_settings_one_current",
            "is_current",
            unique=True,
            postgresql_where=text("is_current IS TRUE"),
        ),
        CheckConstraint("version > 0", name="ck_site_settings_positive_version"),
        CheckConstraint(
            "banner_ends_at IS NULL OR banner_starts_at IS NULL OR banner_ends_at > banner_starts_at",
            name="ck_site_settings_banner_window",
        ),
    )

    version: Mapped[int] = mapped_column(Integer)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    support_email: Mapped[str | None] = mapped_column(String(320))
    footer_text: Mapped[str | None] = mapped_column(String(500))
    public_contact_text: Mapped[str | None] = mapped_column(String(1000))
    social_links_json: Mapped[list[dict]] = mapped_column(JSONB, default=list, nullable=False)
    homepage_announcement: Mapped[str | None] = mapped_column(Text)
    maintenance_notice: Mapped[str | None] = mapped_column(Text)
    banner_level: Mapped[str] = mapped_column(String(24), default="info", nullable=False)
    banner_starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    banner_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    updated_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    reason: Mapped[str] = mapped_column(String(500))
