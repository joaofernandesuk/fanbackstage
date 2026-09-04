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
    snapshots_enabled: bool | None = None
    snapshot_price_minor: int | None = Field(default=None, ge=1)
    private_peeks_enabled: bool | None = None


class CreatorLiveSettingsResponse(BaseModel):
    private_sessions_enabled: bool
    one_to_one_price_minor: int
    two_to_one_price_minor: int
    currency: str
    minimum_minutes: int
    max_authorization_minor: int
    snapshots_enabled: bool
    snapshot_price_minor: int
    private_peeks_enabled: bool


class LivePrivatePeekPolicyInput(BaseModel):
    active: bool
    amount_minor: int = Field(ge=1)
    currency: str = Field(min_length=3, max_length=3)
    commission_basis_points: int = Field(ge=0, le=10_000)
    reason: str = Field(min_length=8, max_length=500)
    confirmed: bool


class LivePrivatePeekPolicyResponse(BaseModel):
    active: bool
    amount_minor: int
    currency: str
    commission_basis_points: int


class LivePrivatePeekOfferResponse(BaseModel):
    paused: bool
    enabled: bool
    amount_minor: int | None = None
    currency: str | None = None
    private_session_id: UUID | None = None
    viewer_admitted: bool = False


class LiveSnapshotOfferResponse(BaseModel):
    enabled: bool
    amount_minor: int
    currency: str


class LiveVipShowInput(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=1000)
    goal_amount_minor: int = Field(ge=1)
    buy_in_amount_minor: int = Field(ge=1)
    preshow_minutes: int = Field(ge=1, le=5)
    duration_minutes: int = Field(ge=5, le=15)


class LiveVipShowResponse(BaseModel):
    id: UUID
    live_room_id: UUID
    status: str
    title: str
    description: str
    goal_amount_minor: int
    confirmed_amount_minor: int
    buy_in_amount_minor: int
    currency: str
    preshow_ends_at: datetime
    duration_seconds: int
    started_at: datetime | None
    ends_at: datetime | None
    viewer_admitted: bool


class PrivateRequestInput(BaseModel):
    mode: str = "one_to_one"
    invited_user_id: UUID | None = None
    note: str | None = Field(default=None, max_length=500)


class ChatInput(BaseModel):
    body: str = Field(min_length=1, max_length=1000)


class LiveTipInput(BaseModel):
    tip_catalog_item_id: UUID


class LiveGiftInput(BaseModel):
    gift_catalog_item_id: UUID


class LiveGiftCatalogItemResponse(BaseModel):
    id: UUID
    name: str
    icon: str
    amount_minor: int
    currency: str
    category: str | None


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


class LiveAudienceMemberResponse(BaseModel):
    user_id: UUID
    label: str
    joined_at: datetime | None


class LiveAudienceResponse(BaseModel):
    current_viewers: int
    peak_viewers: int
    unique_viewers: int
    members: list[LiveAudienceMemberResponse] = Field(default_factory=list)


class LiveFinancialActionSummary(BaseModel):
    event_type: str
    currency: str
    count: int
    amount_minor: int


class LiveCreatorSessionSummaryResponse(BaseModel):
    financial_actions: list[LiveFinancialActionSummary] = Field(default_factory=list)


class LiveTipCatalogItemResponse(BaseModel):
    id: UUID
    label: str
    icon: str
    amount_minor: int
    currency: str


class LiveGoalInput(BaseModel):
    title: str = Field(min_length=1, max_length=140)
    target_amount_minor: int = Field(ge=1)


class LiveGoalUpdate(LiveGoalInput):
    active: bool


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
    peak_viewer_count: int
    started_at: datetime | None
    ended_at: datetime | None
    private_paused: bool = False
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
    invitation_status: str = "not_required"
    invited_viewer_label: str | None = None
    peeks_may_be_available: bool = False


class PrivateInviteCandidateResponse(BaseModel):
    user_id: UUID
    label: str


class PrivateSessionResponse(BaseModel):
    id: UUID
    request_id: UUID
    status: str
    mode: str
    per_minute_price_minor: int
    minimum_charge_minor: int
    max_authorization_minor: int
    currency: str
    billable_seconds: int
    payment_attempt_id: UUID | None
    participant_role: str
    public_live_room_id: UUID | None = None
    peeks_allowed: bool = False
    peek_price_minor: int | None = None
    peek_currency: str | None = None
    peek_commission_basis_points: int | None = None


class ProviderTokenResponse(BaseModel):
    room_id: UUID
    provider_url: str
    token: str
