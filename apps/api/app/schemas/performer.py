from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.compliance import AgeAssuranceLevel


class PerformerCreate(BaseModel):
    safe_reference: str = Field(min_length=2, max_length=255)
    platform_user_id: UUID | None = None
    country_code: str | None = Field(default=None, min_length=2, max_length=2)


class PerformerResponse(BaseModel):
    id: UUID
    safe_reference: str
    platform_user_id: UUID | None
    country_code: str | None


class ContentPerformerLinkInput(BaseModel):
    performer_id: UUID
    consent_release_id: UUID | None = None
    identity_verification_required: bool = True
    age_verification_required: bool = True
    release_required: bool = True


class PerformerVerificationInput(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    provider_reference: str = Field(min_length=1, max_length=255)
    status: str
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    required_minimum_age: int = Field(default=18, ge=1, le=120)
    achieved_assurance_level: AgeAssuranceLevel = AgeAssuranceLevel.high
    expires_at: datetime | None = None
    confirmed: Literal[True]
    reason: str = Field(min_length=8, max_length=500)
