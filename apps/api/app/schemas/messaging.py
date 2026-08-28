from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class MessagingSettingsInput(BaseModel):
    permission: str
    send_fee_minor: int | None = Field(default=None, gt=0)
    send_fee_currency: str | None = Field(default=None, min_length=3, max_length=3)
    subscribers_free: bool = True


class SendMessageInput(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
    reply_to_message_id: UUID | None = None


class AttachmentInput(BaseModel):
    media_asset_id: UUID
    unlock_price_minor: int | None = Field(default=None, gt=0)
    unlock_currency: str | None = Field(default=None, min_length=3, max_length=3)


class AttachmentAccessResponse(BaseModel):
    id: UUID
    media_type: str
    locked: bool
    amount_minor: int | None
    currency: str | None
    preview_delivery_path: str | None
    full_delivery_path: str | None
    adult_access_required: bool = False
    adult_access_granted: bool = True
    compliance_allowed: bool = True
    compliance_code: str = "ALLOWED"
    compliance_action: str | None = None
    compliance_reason: str | None = None


class MessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    sender_user_id: UUID
    body: str | None
    status: str
    created_at: datetime


class ConversationResponse(BaseModel):
    id: UUID
    creator_id: UUID
    viewer_user_id: UUID
    other_user_id: UUID
    last_message_at: datetime | None
    unread_count: int
    archived: bool
    muted: bool


class CampaignInput(BaseModel):
    audience_segment: str
    body: str = Field(min_length=1, max_length=4000)
    scheduled_at: datetime | None = None


class MessageReportInput(BaseModel):
    reason: str = Field(min_length=1, max_length=80)
    details: str | None = Field(default=None, max_length=2000)
