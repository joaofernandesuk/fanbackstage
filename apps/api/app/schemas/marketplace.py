from uuid import UUID

from pydantic import BaseModel, Field


class ShippingAllowanceInput(BaseModel):
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    region_code: str | None = Field(default=None, min_length=1, max_length=16)
    currency: str = Field(min_length=3, max_length=3)
    allowed_shipping_minor: int = Field(ge=0, le=2_147_483_647)
    active: bool = True


class ShippingAllowanceResponse(BaseModel):
    id: UUID
    scope: str
    country_code: str | None
    region_code: str | None
    currency: str
    allowed_shipping_minor: int
    active: bool
