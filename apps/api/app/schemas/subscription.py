from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class PlanPriceInput(BaseModel):
    duration: str
    amount_minor: int = Field(gt=0)
    enabled: bool


class PlanInput(BaseModel):
    currency: str = Field(min_length=3, max_length=3)
    enabled: bool = True
    prices: list[PlanPriceInput] = Field(min_length=1, max_length=4)


class SubscriptionStart(BaseModel):
    duration: str


class AutoRenewInput(BaseModel):
    enabled: bool


class PromotionRuleInput(BaseModel):
    duration: str
    discount_basis_points: int = Field(ge=0, lt=10_000)


class PromotionInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    eligibility: str
    renewal_scope: str = "initial_only"
    enabled: bool = True
    start_at: datetime
    end_at: datetime | None = None
    rules: list[PromotionRuleInput] = Field(min_length=1, max_length=4)


class SubscriptionResponse(BaseModel):
    id: UUID
    creator_id: UUID
    duration: str
    status: str
    currency: str
    auto_renew: bool
    cancel_at_period_end: bool
    current_period_end: datetime | None
    payment_attempt_id: UUID | None = None


class PublicPlanResponse(BaseModel):
    duration: str
    base_amount_minor: int
    effective_amount_minor: int
    currency: str
    discount_basis_points: int
