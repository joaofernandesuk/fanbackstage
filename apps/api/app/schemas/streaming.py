from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class LiveStartInput(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4000)
    access_mode: str = "public"


class PrivateRequestInput(BaseModel):
    mode: str = "one_to_one"
    invited_user_id: UUID | None = None
    note: str | None = Field(default=None, max_length=500)


class ChatInput(BaseModel):
    body: str = Field(min_length=1, max_length=1000)


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


class ProviderTokenResponse(BaseModel):
    room_id: UUID
    provider_url: str
    token: str
