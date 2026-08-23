from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class DiscoveryResult(BaseModel):
    entity_type: str
    id: UUID
    title: str
    subtitle: str | None = None
    description: str | None = None
    creator_id: UUID | None = None
    creator_username: str | None = None
    access_policy: str | None = None
    locked: bool = False
    preview_asset_id: UUID | None = None
    price_amount_minor: int | None = None
    currency: str | None = None
    availability: str | None = None
    live: bool = False
    started_at: datetime | None = None
    created_at: datetime
    reason: str | None = None
    placement_type: str = "organic"
    sponsored: bool = False
    sponsored_surface: str | None = None


class DiscoveryPage(BaseModel):
    items: list[DiscoveryResult]
    next_cursor: str | None = None
    ranking_version: int


class DiscoveryConfigInput(BaseModel):
    text_weight: int = Field(ge=0, le=200)
    live_boost: int = Field(ge=0, le=200)
    recency_weight: int = Field(ge=0, le=200)
    engagement_weight: int = Field(ge=0, le=200)
    trending_window_hours: int = Field(ge=1, le=24 * 30)
    default_result_limit: int = Field(ge=1, le=50)


class DiscoveryHideInput(BaseModel):
    entity_type: str
    entity_id: UUID
    reason: str = Field(min_length=1, max_length=500)
