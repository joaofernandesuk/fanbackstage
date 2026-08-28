from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.compliance.types import PolicyOverrides, PolicyRules, normalize_country_code
from app.models.compliance import (
    AgeAssuranceLevel,
    AgeVerificationStatus,
    ComplianceFeature,
    CompliancePolicyStatus,
)


class CountryResponse(BaseModel):
    code: str
    name: str


class DecisionResponse(BaseModel):
    allowed: bool
    code: str
    action: str | None
    reason: str
    feature: str
    jurisdiction: str | None
    policy_version: int | None
    required_minimum_age: int | None
    required_assurance_level: str
    achieved_assurance_level: str
    age_access_allowed: bool
    feature_allowed: bool
    country_conflict: bool
    verification_expires_at: datetime | None


class AgeVerificationStartInput(BaseModel):
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    return_path: str = Field(default="/", min_length=1, max_length=512)

    @field_validator("country_code")
    @classmethod
    def valid_country(cls, value: str | None) -> str | None:
        return normalize_country_code(value)


class AgeVerificationStartResponse(BaseModel):
    verification_id: UUID
    provider: str
    status: AgeVerificationStatus
    authorization_url: str
    country_code: str
    required_minimum_age: int
    required_assurance_level: AgeAssuranceLevel
    anonymous_session_expires_at: datetime | None


class VerificationSummary(BaseModel):
    verification_id: UUID
    provider: str
    status: AgeVerificationStatus
    country_code: str
    required_minimum_age: int
    required_assurance_level: AgeAssuranceLevel
    achieved_minimum_age: int | None
    achieved_assurance_level: AgeAssuranceLevel
    initiated_at: datetime
    verified_at: datetime | None
    expires_at: datetime | None
    failure_reason_code: str | None
    retryable: bool


class CreatorIdentitySummary(BaseModel):
    status: str
    provider: str
    identity_verified: bool
    adult_verified: bool
    country_code: str | None
    verified_at: datetime | None
    expires_at: datetime | None


class ComplianceStatusResponse(BaseModel):
    fan_age_verification: VerificationSummary | None
    adult_media_decision: DecisionResponse
    creator_identity_verification: CreatorIdentitySummary | None
    payout_eligibility: Literal["not_configured"] = "not_configured"
    performer_verification_issue_count: int | None = None
    consent_release_issue_count: int | None = None


class AnonymousAttachResponse(BaseModel):
    attached: bool = True


class PolicyTemplateInput(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    change_reason: str = Field(min_length=3, max_length=500)


class PolicyTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    key: str
    name: str
    description: str | None


class HighImpactChange(BaseModel):
    change_reason: str = Field(min_length=3, max_length=500)
    confirmation: Literal["CONFIRM_COMPLIANCE_CHANGE"]


class TemplateRevisionInput(HighImpactChange):
    rules: PolicyRules
    status: CompliancePolicyStatus
    effective_from: datetime
    effective_until: datetime | None = None
    reviewed: bool = False
    is_demo: bool = False


class JurisdictionRevisionInput(HighImpactChange):
    template_revision_id: UUID
    overrides: PolicyOverrides = Field(default_factory=PolicyOverrides)
    status: CompliancePolicyStatus
    effective_from: datetime
    effective_until: datetime | None = None
    reviewed: bool = False
    is_demo: bool = False


class FeatureFlagRevisionInput(HighImpactChange):
    feature: ComplianceFeature
    country_scope: str | None = None
    enabled: bool
    effective_from: datetime
    effective_until: datetime | None = None
    is_demo: bool = False

    @field_validator("country_scope")
    @classmethod
    def valid_country(cls, value: str | None) -> str | None:
        return normalize_country_code(value)


class CountryRegistryInput(HighImpactChange):
    code: str
    name: str = Field(min_length=1, max_length=120)

    @field_validator("code")
    @classmethod
    def valid_country(cls, value: str) -> str:
        normalized = normalize_country_code(value)
        assert normalized is not None
        return normalized


class CountryAvailabilityInput(HighImpactChange):
    enabled: bool


class AccountCountryTransitionInput(BaseModel):
    country_code: str
    confirmation: Literal["CONFIRM_COUNTRY_CHANGE"]

    @field_validator("country_code")
    @classmethod
    def valid_country(cls, value: str) -> str:
        normalized = normalize_country_code(value)
        assert normalized is not None
        return normalized


class AccountCountryReviewInput(HighImpactChange):
    country_code: str

    @field_validator("country_code")
    @classmethod
    def valid_country(cls, value: str) -> str:
        normalized = normalize_country_code(value)
        assert normalized is not None
        return normalized


class VerificationReviewInput(HighImpactChange):
    status: Literal["verified", "failed", "revoked", "review_required"]
    achieved_assurance_level: AgeAssuranceLevel | None = None
    achieved_minimum_age: int | None = Field(default=None, ge=1, le=120)
    expires_at: datetime | None = None


class ProviderProbeInput(BaseModel):
    provider: str = Field(min_length=1, max_length=64)


class ComplianceSimulationInput(BaseModel):
    country_code: str
    feature: ComplianceFeature
    user_id: UUID | None = None
    adult_restricted: bool = False

    @field_validator("country_code")
    @classmethod
    def valid_country(cls, value: str) -> str:
        normalized = normalize_country_code(value)
        assert normalized is not None
        return normalized
