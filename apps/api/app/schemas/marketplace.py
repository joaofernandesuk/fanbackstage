from datetime import datetime
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


class MarketplaceHoldPolicyInput(BaseModel):
    hold_duration_seconds: int = Field(ge=0, le=365 * 24 * 60 * 60)
    active: bool = True
    is_default: bool = False


class MarketplaceHoldPolicyResponse(BaseModel):
    id: UUID
    seller_tier: str
    hold_duration_seconds: int
    active: bool
    is_default: bool


class MarketplaceSellerTierInput(BaseModel):
    tier: str
    reason: str = Field(min_length=1, max_length=500)


class MarketplaceSellerTierResponse(BaseModel):
    creator_id: UUID
    tier: str
    marketplace_suspended: bool


class MarketplaceListingCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=5000)
    category: str = Field(min_length=1, max_length=80)
    condition: str
    quantity_available: int = Field(ge=0, le=1_000_000)
    price_amount_minor: int = Field(gt=0, le=2_147_483_647)
    currency: str = Field(min_length=3, max_length=3)
    shipping_mode: str
    origin_country_code: str = Field(min_length=2, max_length=2)
    shipping_charged_minor: int = Field(ge=0, le=2_147_483_647)
    media_asset_ids: list[UUID] = Field(default_factory=list, max_length=12)


class MarketplaceListingResponse(BaseModel):
    id: UUID
    public_id: str
    owner_creator_id: UUID
    title: str
    description: str | None
    category: str
    condition: str
    status: str
    quantity_available: int
    price_amount_minor: int
    currency: str
    shipping_mode: str
    origin_country_code: str
    shipping_charged_minor: int


class MarketplaceCheckoutInput(BaseModel):
    quantity: int = Field(gt=0, le=1_000)
    destination_country_code: str = Field(min_length=2, max_length=2)
    destination_region_code: str | None = Field(default=None, min_length=1, max_length=16)
    shipping_address: "MarketplaceShippingAddressInput"


class MarketplaceShippingAddressInput(BaseModel):
    recipient_name: str = Field(min_length=1, max_length=160)
    line1: str = Field(min_length=1, max_length=160)
    line2: str | None = Field(default=None, max_length=160)
    city: str = Field(min_length=1, max_length=120)
    region_code: str | None = Field(default=None, min_length=1, max_length=16)
    postal_code: str = Field(min_length=1, max_length=32)
    country_code: str = Field(min_length=2, max_length=2)


class MarketplaceShippingAddressResponse(MarketplaceShippingAddressInput):
    order_id: UUID


class MarketplaceOrderResponse(BaseModel):
    id: UUID
    public_id: str
    listing_id: UUID
    status: str
    quantity: int
    currency: str
    item_subtotal_minor: int
    shipping_charged_minor: int
    shipping_allowance_minor: int
    shipping_pass_through_minor: int
    shipping_excess_minor: int
    commissionable_base_minor: int
    platform_fee_minor: int
    creator_amount_minor: int
    group_amount_minor: int
    total_paid_minor: int
    payment_attempt_id: UUID
    carrier: str | None = None
    tracking_reference: str | None = None
    shipped_at: datetime | None = None
    delivered_at: datetime | None = None
    earnings_hold_until: datetime | None = None


class MarketplaceShipmentInput(BaseModel):
    carrier: str | None = Field(default=None, max_length=120)
    tracking_reference: str | None = Field(default=None, max_length=255)


class MarketplaceTrackingEventResponse(BaseModel):
    event_type: str
    carrier: str | None
    tracking_reference: str | None
    created_at: datetime
