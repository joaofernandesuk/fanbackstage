from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class TaxonomyItem(BaseModel):
    id: UUID
    code: str
    label: str


class SocialLinkInput(BaseModel):
    label: str = Field(min_length=1, max_length=48)
    url: HttpUrl


class CreatorProfileUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=32)
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    bio: str | None = Field(default=None, max_length=2000)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    region: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=80)
    show_location: bool | None = None
    timezone: str | None = Field(default=None, max_length=64)
    language_codes: list[str] | None = Field(default=None, max_length=12)
    category_slugs: list[str] | None = Field(default=None, max_length=12)
    social_links: list[SocialLinkInput] | None = Field(default=None, max_length=12)
    is_public: bool | None = None


class CreatorUsernameAvailabilityResponse(BaseModel):
    username: str
    available: bool


class CreatorComplianceEligibilityResponse(BaseModel):
    jurisdiction: str | None
    policy_version: int | None
    verification_status: str | None
    verification_expires_at: datetime | None
    identity_required: bool
    identity_allowed: bool
    age_required: bool
    age_allowed: bool
    public_allowed: bool
    payout_kyc_required: bool
    payout_kyc_satisfied: bool
    payout_allowed: bool
    code: str
    reason: str
    payout_code: str


class CreatorSelfResponse(BaseModel):
    id: UUID
    username: str | None
    display_name: str | None
    bio: str | None
    country_code: str | None
    region: str | None
    city: str | None
    show_location: bool
    timezone: str | None
    status: str
    is_public: bool
    verification_status: str
    adult_verified: bool
    creator_compliance: CreatorComplianceEligibilityResponse
    performer_consent_issue_count: int
    creator_compliance_action_required: bool
    rejection_reason: str | None
    languages: list[TaxonomyItem]
    categories: list[TaxonomyItem]
    social_links: list[SocialLinkInput]
    available_languages: list[TaxonomyItem]
    available_categories: list[TaxonomyItem]
    development_verification_available: bool
    staging_kyc_sandbox_available: bool
    staging_kyc_session_reference: str | None = None
    staging_kyc_verification_id: UUID | None = None


class StagingKycOutcomeInput(BaseModel):
    outcome: str = Field(pattern="^(VERIFIED|FAILED|REVIEW_REQUIRED|EXPIRED)$")


class PublicCreatorResponse(BaseModel):
    id: UUID
    username: str
    display_name: str
    bio: str | None
    avatar_reference: str | None
    cover_reference: str | None
    location: str | None
    timezone: str | None
    verified: bool
    follower_count: int
    languages: list[TaxonomyItem]
    categories: list[TaxonomyItem]
    social_links: list[SocialLinkInput]
    adult_access_required: bool = True
    adult_access_granted: bool = False
    compliance_allowed: bool = False
    compliance_code: str = "POLICY_UNAVAILABLE"
    compliance_action: str | None = None
    compliance_reason: str | None = None
