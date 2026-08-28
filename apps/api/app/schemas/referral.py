from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class ConfirmedReferralAdminChange(BaseModel):
    reason: str = Field(min_length=8, max_length=500)
    confirmed: Literal[True]

    @field_validator("reason")
    @classmethod
    def reason_must_be_meaningful(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 8:
            raise ValueError("Reason must contain at least 8 non-whitespace characters")
        return normalized


class AffiliatePartnerInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    external_reference: str | None = Field(default=None, max_length=255)
    owner_user_id: UUID | None = None


class AffiliatePartnerResponse(BaseModel):
    id: UUID
    public_id: str
    name: str
    status: str


class AffiliatePartnerStatusInput(BaseModel):
    status: str = Field(pattern="^(active|paused|suspended|terminated)$")


class ReferralProgramInput(ConfirmedReferralAdminChange):
    actor_type: str
    program_type: str
    owner_user_id: UUID | None = None
    owner_creator_id: UUID | None = None
    affiliate_partner_id: UUID | None = None
    terms_reference: str | None = Field(default=None, max_length=255)


class ReferralPolicyInput(ConfirmedReferralAdminChange):
    basis_points: int = Field(ge=0, le=10_000)
    eligible_revenue_types: list[str] = Field(min_length=1, max_length=8)
    attribution_window_days: int = Field(default=30, ge=1, le=365)
    subscription_reward_window_days: int = Field(default=90, ge=1, le=365)


class ReferralLinkInput(ConfirmedReferralAdminChange):
    policy_id: UUID
    code: str = Field(min_length=1, max_length=64)
    destination_path: str = Field(min_length=1, max_length=512)
    source: str | None = Field(default=None, max_length=80)
    expires_at: datetime | None = None


class ReferralLinkResponse(BaseModel):
    public_id: str
    code: str
    destination_path: str
    status: str
    policy_id: UUID
