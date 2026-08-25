from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class NotificationResponse(BaseModel):
    id: UUID
    notification_type: str
    title: str
    body: str
    target_path: str | None
    created_at: datetime
    read_at: datetime | None


class NotificationPage(BaseModel):
    items: list[NotificationResponse]
    unread_count: int


class PreferenceInput(BaseModel):
    email_enabled: bool
    in_app_enabled: bool = True
    consent: bool = False


class PreferenceResponse(BaseModel):
    category: str
    email_enabled: bool
    in_app_enabled: bool
    consented_at: datetime | None


class ProviderWebhookInput(BaseModel):
    provider_message_id: str = Field(min_length=1, max_length=255)
    event: str = Field(pattern="^(delivered|hard_bounce|complaint)$")
