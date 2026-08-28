from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class LiveStartInput(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    access_mode: str = "public"


class CreatorLiveSettingsInput(BaseModel):
    private_sessions_enabled: bool | None = None
    one_to_one_price_minor: int | None = Field(default=None, ge=1)
    two_to_one_price_minor: int | None = Field(default=None, ge=1)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    minimum_minutes: int | None = Field(default=None, ge=1, le=120)
    max_authorization_minor: int | None = Field(default=None, ge=1)


class CreatorLiveSettingsResponse(BaseModel):
    private_sessions_enabled: bool
    one_to_one_price_minor: int
    two_to_one_price_minor: int
    currency: str
    minimum_minutes: int
    max_authorization_minor: int


class PrivateRequestInput(BaseModel):
    mode: str = "one_to_one"
    invited_user_id: UUID | None = None
    note: str | None = Field(default=None, max_length=500)


class ChatInput(BaseModel):
    body: str = Field(min_length=1, max_length=1000)


class LiveBanInput(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class LiveReportInput(BaseModel):
    reason: str = Field(min_length=1, max_length=120)
    details: str | None = Field(default=None, max_length=1000)
    chat_message_id: UUID | None = None


class LiveRoomResponse(BaseModel):
    id: UUID
    public_id: str
    creator_id: UUID
    status: str
    access_mode: str
    title: str
    description: str | None
    viewer_count: int
    started_at: datetime | None
    ended_at: datetime | None
    adult_access_required: bool = True
    adult_access_granted: bool = False
    compliance_allowed: bool = False
    compliance_code: str = "AGE_VERIFICATION_REQUIRED"
    compliance_action: str | None = "VERIFY_AGE"
    compliance_reason: str | None = None


class PrivateRequestResponse(BaseModel):
    id: UUID
    creator_id: UUID
    status: str
    mode: str
    per_minute_price_minor: int
    minimum_charge_minor: int
    currency: str
    expires_at: datetime


class PrivateSessionResponse(BaseModel):
    id: UUID
    request_id: UUID
    status: str
    mode: str
    per_minute_price_minor: int
    minimum_charge_minor: int
    currency: str
    billable_seconds: int
    payment_attempt_id: UUID | None


class ProviderTokenResponse(BaseModel):
    room_id: UUID
    provider_url: str
    token: str
