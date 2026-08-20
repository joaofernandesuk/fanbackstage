from uuid import UUID

from pydantic import BaseModel, Field


class PurchaseResponse(BaseModel):
    id: UUID
    content_id: UUID
    status: str
    gross_amount_minor: int
    platform_fee_minor: int
    creator_amount_minor: int
    currency: str
    payment_attempt_id: UUID


class RefundRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class CommissionUpdate(BaseModel):
    basis_points: int = Field(ge=0, le=10_000)


class CreatorEarningsResponse(BaseModel):
    pending_amount_minor: int
    available_amount_minor: int
    currency: str
