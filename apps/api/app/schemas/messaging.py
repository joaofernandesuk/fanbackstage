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
    last_message_at: datetime | None
    unread_count: int


class CampaignInput(BaseModel):
    audience_segment: str
    body: str = Field(min_length=1, max_length=4000)
    scheduled_at: datetime | None = None
