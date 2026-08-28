"""Compliance, age-assurance, jurisdiction, and private performer records.

The records in this module deliberately keep viewer age assurance, creator
identity/KYC, performer verification, consent, authentication, and commercial
entitlement as separate authorities.
"""

from __future__ import annotations

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
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamped, UUIDPrimaryKey


class AgeAssuranceLevel(str, enum.Enum):
    none = "none"
    self_attested = "self_attested"
    low = "low"
    medium = "medium"
    high = "high"


class AgeVerificationStatus(str, enum.Enum):
    pending = "pending"
    verified = "verified"
    failed = "failed"
    expired = "expired"
    revoked = "revoked"
    review_required = "review_required"


class CompliancePolicyStatus(str, enum.Enum):
    draft = "draft"
    scheduled = "scheduled"
    active = "active"
    retired = "retired"


class ComplianceFeature(str, enum.Enum):
    platform_access = "platform_access"
    new_fan_registration = "new_fan_registration"
    creator_registration = "creator_registration"
    purchases = "purchases"
    subscriptions = "subscriptions"
    ppv = "ppv"
    live = "live"
    marketplace = "marketplace"
    featuring = "featuring"
    marketing_email = "marketing_email"
    messaging = "messaging"
    adult_media = "adult_media"


class ProviderProbeStatus(str, enum.Enum):
    healthy = "healthy"
    degraded = "degraded"
    unavailable = "unavailable"
    misconfigured = "misconfigured"


class ProviderCallbackStatus(str, enum.Enum):
    received = "received"
    processed = "processed"
    rejected = "rejected"


class PerformerIdentityStatus(str, enum.Enum):
    not_started = "not_started"
    pending = "pending"
    verified = "verified"
    failed = "failed"
    review_required = "review_required"
    expired = "expired"
    revoked = "revoked"
    suspended = "suspended"


class CountryRegistry(Timestamped, Base):
    __tablename__ = "country_registry"
    __table_args__ = (
        CheckConstraint("code ~ '^[A-Z]{2}$'", name="ck_country_registry_code"),
        CheckConstraint("length(btrim(name)) > 0", name="ck_country_registry_name"),
    )

    code: Mapped[str] = mapped_column(String(2), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class CompliancePolicyTemplate(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "compliance_policy_templates"

    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(String(500))


class CompliancePolicyTemplateRevision(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "compliance_policy_template_revisions"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_compliance_template_revision_version"),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_compliance_template_revision_window",
        ),
        CheckConstraint(
            "(reviewed_at IS NULL AND reviewed_by_user_id IS NULL) OR "
            "(reviewed_at IS NOT NULL AND reviewed_by_user_id IS NOT NULL)",
            name="ck_compliance_template_revision_review",
        ),
        UniqueConstraint("template_id", "version", name="uq_compliance_template_revision_version"),
        Index(
            "ix_compliance_template_revision_effective",
            "template_id",
            "status",
            "effective_from",
        ),
    )

    template_id: Mapped[UUID] = mapped_column(
        ForeignKey("compliance_policy_templates.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[CompliancePolicyStatus] = mapped_column(
        Enum(CompliancePolicyStatus, name="compliance_policy_status"), index=True
    )
    rules_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    change_reason: Mapped[str] = mapped_column(String(500))


class JurisdictionPolicyRevision(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "jurisdiction_policy_revisions"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_jurisdiction_policy_revision_version"),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_jurisdiction_policy_revision_window",
        ),
        CheckConstraint(
            "(reviewed_at IS NULL AND reviewed_by_user_id IS NULL) OR "
            "(reviewed_at IS NOT NULL AND reviewed_by_user_id IS NOT NULL)",
            name="ck_jurisdiction_policy_revision_review",
        ),
        UniqueConstraint("country_code", "version", name="uq_jurisdiction_policy_country_version"),
        Index(
            "ix_jurisdiction_policy_effective",
            "country_code",
            "status",
            "effective_from",
        ),
    )

    country_code: Mapped[str] = mapped_column(
        ForeignKey("country_registry.code", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    template_revision_id: Mapped[UUID] = mapped_column(
        ForeignKey("compliance_policy_template_revisions.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[CompliancePolicyStatus] = mapped_column(
        Enum(CompliancePolicyStatus, name="compliance_policy_status", create_type=False), index=True
    )
    overrides_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    change_reason: Mapped[str] = mapped_column(String(500))


class FeatureFlagRevision(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "feature_flag_revisions"
    __table_args__ = (
        CheckConstraint("version > 0", name="ck_feature_flag_revision_version"),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_feature_flag_revision_window",
        ),
        CheckConstraint(
            "country_scope = '' OR country_scope ~ '^[A-Z]{2}$'",
            name="ck_feature_flag_country_scope",
        ),
        UniqueConstraint(
            "feature", "country_scope", "version", name="uq_feature_flag_scope_version"
        ),
        Index(
            "ix_feature_flag_effective",
            "feature",
            "country_scope",
            "effective_from",
        ),
    )

    feature: Mapped[ComplianceFeature] = mapped_column(
        Enum(ComplianceFeature, name="compliance_feature"), index=True
    )
    # Empty string is the global scope. A concrete scope must be an ISO alpha-2 code.
    country_scope: Mapped[str] = mapped_column(String(2), default="", nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    change_reason: Mapped[str] = mapped_column(String(500))


class AnonymousComplianceSession(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "anonymous_compliance_sessions"
    __table_args__ = (
        CheckConstraint(
            "(attached_user_id IS NULL AND attached_at IS NULL) OR "
            "(attached_user_id IS NOT NULL AND attached_at IS NOT NULL)",
            name="ck_anonymous_compliance_session_attachment",
        ),
    )

    secret_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    attached_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    attached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgeVerificationRecord(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "age_verification_records"
    __table_args__ = (
        CheckConstraint(
            "user_id IS NOT NULL OR anonymous_session_id IS NOT NULL",
            name="ck_age_verification_subject",
        ),
        CheckConstraint(
            "required_minimum_age > 0 AND required_minimum_age <= 120",
            name="ck_age_verification_minimum_age",
        ),
        CheckConstraint(
            "expires_at IS NULL OR verified_at IS NULL OR expires_at > verified_at",
            name="ck_age_verification_expiry",
        ),
        CheckConstraint(
            "status <> 'verified' OR "
            "(verified_at IS NOT NULL AND achieved_assurance_level <> 'none' "
            "AND achieved_minimum_age IS NOT NULL AND expires_at IS NOT NULL)",
            name="ck_age_verification_verified_complete",
        ),
        CheckConstraint(
            "status <> 'failed' OR failed_at IS NOT NULL",
            name="ck_age_verification_failed_complete",
        ),
        CheckConstraint(
            "status <> 'revoked' OR revoked_at IS NOT NULL",
            name="ck_age_verification_revoked_complete",
        ),
        UniqueConstraint(
            "provider", "provider_verification_id", name="uq_age_verification_provider_reference"
        ),
        UniqueConstraint("state_hash", name="uq_age_verification_state_hash"),
        Index("ix_age_verification_user_status", "user_id", "status", "created_at"),
        Index(
            "ix_age_verification_anonymous_status",
            "anonymous_session_id",
            "status",
            "created_at",
        ),
    )

    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    anonymous_session_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("anonymous_compliance_sessions.id", ondelete="RESTRICT"), index=True
    )
    provider: Mapped[str] = mapped_column(String(64), index=True)
    provider_verification_id: Mapped[str | None] = mapped_column(String(255))
    state_hash: Mapped[str] = mapped_column(String(64))
    state_consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    safe_return_path: Mapped[str] = mapped_column(String(512), default="/", nullable=False)
    country_code: Mapped[str] = mapped_column(
        ForeignKey("country_registry.code", ondelete="RESTRICT"), index=True
    )
    applicable_policy_id: Mapped[UUID] = mapped_column(
        ForeignKey("jurisdiction_policy_revisions.id", ondelete="RESTRICT"), index=True
    )
    applicable_policy_version: Mapped[int] = mapped_column(Integer)
    required_minimum_age: Mapped[int] = mapped_column(Integer)
    achieved_minimum_age: Mapped[int | None] = mapped_column(Integer)
    required_assurance_level: Mapped[AgeAssuranceLevel] = mapped_column(
        Enum(AgeAssuranceLevel, name="age_assurance_level")
    )
    achieved_assurance_level: Mapped[AgeAssuranceLevel] = mapped_column(
        Enum(AgeAssuranceLevel, name="age_assurance_level", create_type=False),
        default=AgeAssuranceLevel.none,
        nullable=False,
    )
    status: Mapped[AgeVerificationStatus] = mapped_column(
        Enum(AgeVerificationStatus, name="age_verification_status"),
        default=AgeVerificationStatus.pending,
        index=True,
    )
    initiated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    failure_reason_code: Mapped[str | None] = mapped_column(String(96))
    retryable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    result_metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class AgeProviderCallbackEvent(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "age_provider_callback_events"
    __table_args__ = (
        UniqueConstraint("provider", "external_event_id", name="uq_age_provider_callback_external"),
    )

    provider: Mapped[str] = mapped_column(String(64), index=True)
    external_event_id: Mapped[str] = mapped_column(String(255))
    verification_record_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("age_verification_records.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[ProviderCallbackStatus] = mapped_column(
        Enum(ProviderCallbackStatus, name="provider_callback_status"), index=True
    )
    failure_reason_code: Mapped[str | None] = mapped_column(String(96))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgeProviderProbe(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "age_provider_probes"

    provider: Mapped[str] = mapped_column(String(64), index=True)
    environment: Mapped[str] = mapped_column(String(32))
    status: Mapped[ProviderProbeStatus] = mapped_column(
        Enum(ProviderProbeStatus, name="provider_probe_status"), index=True
    )
    capabilities_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    configuration_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    callback_url: Mapped[str | None] = mapped_column(String(512))
    error_code: Mapped[str | None] = mapped_column(String(96))
    probed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class PerformerIdentity(UUIDPrimaryKey, Timestamped, Base):
    """Private participant identity; never a public profile projection."""

    __tablename__ = "performer_identities"
    __table_args__ = (
        UniqueConstraint("owner_creator_id", "safe_reference", name="uq_performer_owner_reference"),
    )

    owner_creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    platform_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    safe_reference: Mapped[str] = mapped_column(String(255))
    country_code: Mapped[str | None] = mapped_column(
        ForeignKey("country_registry.code", ondelete="RESTRICT")
    )
    created_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))


class PerformerIdentityVerification(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "performer_identity_verifications"
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_reference", name="uq_performer_identity_provider_reference"
        ),
    )

    performer_id: Mapped[UUID] = mapped_column(
        ForeignKey("performer_identities.id", ondelete="RESTRICT"), index=True
    )
    provider: Mapped[str] = mapped_column(String(64))
    provider_reference: Mapped[str] = mapped_column(String(255))
    status: Mapped[PerformerIdentityStatus] = mapped_column(
        Enum(PerformerIdentityStatus, name="performer_identity_status"), index=True
    )
    country_code: Mapped[str | None] = mapped_column(
        ForeignKey("country_registry.code", ondelete="RESTRICT")
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason_code: Mapped[str | None] = mapped_column(String(96))
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class PerformerAgeVerification(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "performer_age_verifications"
    __table_args__ = (
        UniqueConstraint(
            "provider", "provider_reference", name="uq_performer_age_provider_reference"
        ),
        CheckConstraint(
            "required_minimum_age > 0 AND required_minimum_age <= 120",
            name="ck_performer_age_minimum_age",
        ),
    )

    performer_id: Mapped[UUID] = mapped_column(
        ForeignKey("performer_identities.id", ondelete="RESTRICT"), index=True
    )
    provider: Mapped[str] = mapped_column(String(64))
    provider_reference: Mapped[str] = mapped_column(String(255))
    status: Mapped[AgeVerificationStatus] = mapped_column(
        Enum(AgeVerificationStatus, name="age_verification_status", create_type=False), index=True
    )
    country_code: Mapped[str] = mapped_column(
        ForeignKey("country_registry.code", ondelete="RESTRICT")
    )
    required_minimum_age: Mapped[int] = mapped_column(Integer)
    achieved_assurance_level: Mapped[AgeAssuranceLevel] = mapped_column(
        Enum(AgeAssuranceLevel, name="age_assurance_level", create_type=False)
    )
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason_code: Mapped[str | None] = mapped_column(String(96))
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class VerifiedContentPerformer(UUIDPrimaryKey, Timestamped, Base):
    """Private verification authority linked to the existing public performer join."""

    __tablename__ = "verified_content_performers"
    __table_args__ = (UniqueConstraint("content_id", "performer_id", name="uq_content_performer"),)

    content_id: Mapped[UUID] = mapped_column(
        ForeignKey("content_items.id", ondelete="RESTRICT"), index=True
    )
    performer_id: Mapped[UUID] = mapped_column(
        ForeignKey("performer_identities.id", ondelete="RESTRICT"), index=True
    )
    consent_release_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("consent_releases.id", ondelete="RESTRICT"), index=True
    )
    identity_verification_required: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    age_verification_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    release_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
