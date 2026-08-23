from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SurfaceInput(BaseModel):
    kind: str
    cancellation_cutoff_seconds: int = Field(default=3600, ge=0, le=30 * 24 * 3600)


class SlotInput(BaseModel):
    surface_id: UUID
    slot_key: str = Field(min_length=1, max_length=64)
    position: int = Field(ge=0)
    capacity: int = Field(default=1, ge=1, le=20)


class PriceInput(BaseModel):
    slot_id: UUID
    target_type: str
    duration_seconds: int = Field(gt=0, le=7 * 24 * 3600)
    amount_minor: int = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)


class BookingInput(BaseModel):
    slot_id: UUID
    target_type: str
    target_id: UUID
    starts_at: datetime
    duration_seconds: int = Field(gt=0, le=7 * 24 * 3600)
    payer_user_id: UUID | None = None
