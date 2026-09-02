from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.content import AccessPolicy


class FeedPostInput(BaseModel):
    post_type: str = "text"
    body: str | None = Field(default=None, max_length=5000)
    access_policy: AccessPolicy = AccessPolicy.free
    media_asset_ids: list[UUID] = Field(default_factory=list, max_length=20)
    content_id: UUID | None = None
    comments_enabled: bool = True
    reactions_enabled: bool = True
    scheduled_at: datetime | None = None


class FeedPostUpdate(BaseModel):
    body: str | None = Field(default=None, max_length=5000)
    access_policy: AccessPolicy | None = None
    comments_enabled: bool | None = None
    reactions_enabled: bool | None = None
    scheduled_at: datetime | None = None


class ReactionInput(BaseModel):
    reaction_type: str = "like"


class CommentInput(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    parent_id: UUID | None = None


class ReportInput(BaseModel):
    reason: str = Field(min_length=1, max_length=80)
    details: str | None = Field(default=None, max_length=2000)


class FeedSettingsInput(BaseModel):
    auto_post_galleries: bool | None = None
    auto_post_videos: bool | None = None
    default_comments_enabled: bool | None = None


class FeedPostResponse(BaseModel):
    id: UUID
    creator_id: UUID
    creator_username: str
    creator_name: str
    post_type: str
    body: str | None
    status: str
    access_policy: str
    locked: bool
    published_at: datetime | None
    pinned_at: datetime | None
    comments_enabled: bool
    reactions_enabled: bool
    reaction_count: int
    reaction_counts: dict[str, int] = Field(default_factory=dict)
    comment_count: int
    viewer_reaction: str | None = None
    media: list[dict] = Field(default_factory=list)
    content_reference: dict | None = None
    adult_access_required: bool = False
    adult_access_granted: bool = True
    compliance_allowed: bool = True
    compliance_code: str = "ALLOWED"
    compliance_action: str | None = None
    compliance_reason: str | None = None


class FeedPage(BaseModel):
    items: list[FeedPostResponse]
    next_cursor: str | None = None
    compliance_allowed: bool = True
    compliance_code: str = "ALLOWED"
    compliance_action: str | None = None
    compliance_reason: str | None = None
