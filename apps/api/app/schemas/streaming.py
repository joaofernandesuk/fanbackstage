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


class LiveTipInput(BaseModel):
    amount_minor: int | None = Field(default=None, ge=1)
    tip_menu_item_id: UUID | None = None


class LiveGiftInput(BaseModel):
    gift_catalog_item_id: UUID


class LiveCommerceResponse(BaseModel):
    id: UUID
    status: str
    kind: str
    gross_amount_minor: int
    currency: str
    payment_attempt_id: UUID
    request_label: str | None = None
    request_message: str | None = None
    expires_at: datetime | None = None
    resolved_at: datetime | None = None


class PaidRequestInput(BaseModel):
    option_id: UUID
    message: str = Field(min_length=1, max_length=500)


class PaidRequestOptionInput(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    amount_minor: int = Field(ge=1)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0, le=100)
    requires_creator_acceptance: bool = True


class PaidRequestOptionResponse(PaidRequestOptionInput):
    id: UUID
    currency: str


class LiveReactionInput(BaseModel):
    reaction_type: str


class LiveReactionSummaryResponse(BaseModel):
    counts: dict[str, int] = Field(default_factory=dict)


class LiveSupporterRankingEntry(BaseModel):
    rank: int
    amount_minor: int
    currency: str
    supporter_label: str
    viewer_is_current_user: bool


class TipMenuItemInput(BaseModel):
    label: str = Field(min_length=1, max_length=100)
    amount_minor: int = Field(ge=1)
    enabled: bool = True
    sort_order: int = Field(default=0, ge=0, le=100)


class TipMenuItemResponse(TipMenuItemInput):
    id: UUID
    currency: str


class LiveGoalInput(BaseModel):
    title: str = Field(min_length=1, max_length=140)
    target_amount_minor: int = Field(ge=1)


class LiveGoalResponse(LiveGoalInput):
    id: UUID
    currency: str
    active: bool
    progress_amount_minor: int = 0


class LiveEventResponse(BaseModel):
    id: UUID
    event_type: str
    actor_user_id: UUID | None
    amount_minor: int | None
    currency: str | None
    source_type: str | None
    source_id: str | None
    metadata: dict = Field(default_factory=dict)
    occurred_at: datetime
    created_at: datetime


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
