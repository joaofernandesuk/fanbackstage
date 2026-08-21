"""Physical-goods marketplace state and immutable commercial order snapshots."""

import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamped, UUIDPrimaryKey
from app.models.content import ModerationStatus


class MarketplaceListingStatus(str, enum.Enum):
    draft = "draft"
    pending_review = "pending_review"
    published = "published"
    paused = "paused"
    sold_out = "sold_out"
    rejected = "rejected"
    removed = "removed"
    archived = "archived"


class MarketplaceCondition(str, enum.Enum):
    new = "new"
    like_new = "like_new"
    used = "used"
    personal_worn = "personal_worn"


class MarketplaceShippingMode(str, enum.Enum):
    domestic = "domestic"
    selected_countries = "selected_countries"
    worldwide = "worldwide"


class ShippingAllowanceScope(str, enum.Enum):
    country = "country"
    region = "region"
    country_region = "country_region"
    global_ = "global"


class MarketplaceOrderStatus(str, enum.Enum):
    awaiting_payment = "awaiting_payment"
    paid = "paid"
    processing = "processing"
    shipped = "shipped"
    delivered = "delivered"
    cancelled = "cancelled"
    refunded = "refunded"
    disputed = "disputed"
    chargeback = "chargeback"


class MarketplaceListing(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "marketplace_listings"
    __table_args__ = (
        CheckConstraint("quantity_available >= 0", name="ck_marketplace_listing_nonnegative_stock"),
        CheckConstraint("price_amount_minor > 0", name="ck_marketplace_listing_positive_price"),
        CheckConstraint(
            "shipping_charged_minor >= 0", name="ck_marketplace_listing_nonnegative_shipping"
        ),
    )

    public_id: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    owner_creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(80))
    condition: Mapped[MarketplaceCondition] = mapped_column(
        Enum(MarketplaceCondition, name="marketplace_condition"), index=True
    )
    status: Mapped[MarketplaceListingStatus] = mapped_column(
        Enum(MarketplaceListingStatus, name="marketplace_listing_status"),
        default=MarketplaceListingStatus.draft,
        index=True,
    )
    moderation_status: Mapped[ModerationStatus] = mapped_column(
        Enum(ModerationStatus, name="moderation_status", create_type=False),
        default=ModerationStatus.not_reviewed,
        index=True,
    )
    quantity_available: Mapped[int] = mapped_column(Integer)
    price_amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    shipping_mode: Mapped[MarketplaceShippingMode] = mapped_column(
        Enum(MarketplaceShippingMode, name="marketplace_shipping_mode")
    )
    origin_country_code: Mapped[str] = mapped_column(String(2))
    shipping_charged_minor: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MarketplaceListingMedia(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "marketplace_listing_media"
    __table_args__ = (
        UniqueConstraint("listing_id", "position", name="uq_marketplace_listing_media_position"),
        UniqueConstraint("listing_id", "media_asset_id", name="uq_marketplace_listing_media_asset"),
    )

    listing_id: Mapped[UUID] = mapped_column(
        ForeignKey("marketplace_listings.id", ondelete="CASCADE"), index=True
    )
    media_asset_id: Mapped[UUID] = mapped_column(
        ForeignKey("media_assets.id", ondelete="RESTRICT"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)


class MarketplaceShippingAllowance(UUIDPrimaryKey, Timestamped, Base):
    """Server-controlled reasonable shipping allowance keyed by geography and currency."""

    __tablename__ = "marketplace_shipping_allowances"
    __table_args__ = (
        CheckConstraint("allowed_shipping_minor >= 0", name="ck_shipping_allowance_nonnegative"),
        UniqueConstraint(
            "scope",
            "destination_code",
            "currency",
            name="uq_shipping_allowance_destination_currency",
        ),
    )

    scope: Mapped[ShippingAllowanceScope] = mapped_column(
        Enum(
            ShippingAllowanceScope,
            name="shipping_allowance_scope",
            values_callable=lambda enum_class: [member.value for member in enum_class],
        ),
        index=True,
    )
    destination_code: Mapped[str] = mapped_column(String(16), index=True)
    country_code: Mapped[str | None] = mapped_column(String(2), index=True)
    region_code: Mapped[str | None] = mapped_column(String(16), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    allowed_shipping_minor: Mapped[int] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(default=True, nullable=False)


class MarketplaceOrder(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "marketplace_orders"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_marketplace_order_positive_quantity"),
        CheckConstraint("item_subtotal_minor > 0", name="ck_marketplace_order_positive_subtotal"),
        CheckConstraint(
            "shipping_charged_minor >= 0", name="ck_marketplace_order_nonnegative_shipping"
        ),
        CheckConstraint(
            "shipping_allowance_minor >= 0", name="ck_marketplace_order_nonnegative_allowance"
        ),
        CheckConstraint(
            "shipping_pass_through_minor >= 0", name="ck_marketplace_order_nonnegative_pass_through"
        ),
        CheckConstraint(
            "shipping_excess_minor >= 0", name="ck_marketplace_order_nonnegative_excess"
        ),
        CheckConstraint(
            "commissionable_base_minor > 0",
            name="ck_marketplace_order_positive_commissionable_base",
        ),
        CheckConstraint("platform_fee_minor >= 0", name="ck_marketplace_order_nonnegative_fee"),
        CheckConstraint(
            "creator_amount_minor >= 0", name="ck_marketplace_order_nonnegative_creator"
        ),
        CheckConstraint("group_amount_minor >= 0", name="ck_marketplace_order_nonnegative_group"),
        CheckConstraint("total_paid_minor > 0", name="ck_marketplace_order_positive_total"),
        CheckConstraint(
            "shipping_pass_through_minor + shipping_excess_minor = shipping_charged_minor",
            name="ck_marketplace_order_shipping_balance",
        ),
        CheckConstraint(
            "commissionable_base_minor = item_subtotal_minor + shipping_excess_minor",
            name="ck_marketplace_order_commissionable_base",
        ),
        CheckConstraint(
            "total_paid_minor = item_subtotal_minor + shipping_charged_minor",
            name="ck_marketplace_order_total_paid",
        ),
        CheckConstraint(
            "commissionable_base_minor = platform_fee_minor + creator_amount_minor + group_amount_minor",
            name="ck_marketplace_order_commissionable_balance",
        ),
    )

    public_id: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    listing_id: Mapped[UUID] = mapped_column(
        ForeignKey("marketplace_listings.id", ondelete="RESTRICT"), index=True
    )
    buyer_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    seller_creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    quantity: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    destination_country_code: Mapped[str] = mapped_column(String(2))
    item_subtotal_minor: Mapped[int] = mapped_column(Integer)
    shipping_charged_minor: Mapped[int] = mapped_column(Integer)
    shipping_allowance_minor: Mapped[int] = mapped_column(Integer)
    shipping_pass_through_minor: Mapped[int] = mapped_column(Integer)
    shipping_excess_minor: Mapped[int] = mapped_column(Integer)
    commissionable_base_minor: Mapped[int] = mapped_column(Integer)
    platform_fee_minor: Mapped[int] = mapped_column(Integer)
    creator_amount_minor: Mapped[int] = mapped_column(Integer)
    group_amount_minor: Mapped[int] = mapped_column(Integer)
    total_paid_minor: Mapped[int] = mapped_column(Integer)
    commission_basis_points: Mapped[int] = mapped_column(Integer)
    status: Mapped[MarketplaceOrderStatus] = mapped_column(
        Enum(MarketplaceOrderStatus, name="marketplace_order_status"),
        default=MarketplaceOrderStatus.awaiting_payment,
        index=True,
    )
    reservation_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payment_attempt_id: Mapped[UUID] = mapped_column(
        ForeignKey("payment_attempts.id", ondelete="RESTRICT"), unique=True
    )
    ledger_transaction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), unique=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
