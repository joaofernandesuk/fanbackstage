from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.content import AccessPolicy


class StoryCreate(BaseModel):
    media_asset_id: UUID
    caption: str | None = Field(default=None, max_length=2000)
    alt_text: str | None = Field(default=None, max_length=500)
    access_policy: AccessPolicy = AccessPolicy.free


class StoryCreatorResponse(BaseModel):
    id: UUID
    username: str
    display_name: str
    avatar_reference: str | None
    verified: bool


class StoryMediaResponse(BaseModel):
    derivative_id: UUID
    mime_type: str
    delivery_path: str


class StoryResponse(BaseModel):
    id: UUID
    status: str
    creator: StoryCreatorResponse
    media_type: str
    caption: str | None
    alt_text: str | None
    access_policy: str
    created_at: datetime
    published_at: datetime
    expires_at: datetime
    media: StoryMediaResponse


class StoryRailResponse(BaseModel):
    items: list[StoryResponse]
    next_cursor: str | None = None
