"""Server-authoritative physical marketplace checkout and settlement."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.core.config import get_settings
from app.finance.service import (
    _account,
    commission_amount,
    commission_for,
    creator_revenue_allocation,
    currency_code,
    post_entries,
)
from app.models.content import ModerationStatus
from app.models.creator import CreatorProfile
from app.models.finance import (
    LedgerAccountKind,
    LedgerDirection,
    LedgerTransactionType,
    PaymentAttempt,
    PaymentStatus,
)
from app.models.identity import User
from app.models.marketplace import (
    MarketplaceListing,
    MarketplaceListingStatus,
    MarketplaceOrder,
    MarketplaceOrderStatus,
    MarketplaceShippingAllowance,
    ShippingAllowanceScope,
)


class MarketplaceError(ValueError):
    pass


# The table is intentionally able to hold regional allowances.  This small,
# server-owned map is the Phase 9 implementation boundary; it can later be
# replaced by a carrier/parcel-class service without accepting creator input.
_EU_COUNTRIES = frozenset(
    {
        "AT",
        "BE",
        "BG",
        "HR",
        "CY",
        "CZ",
        "DK",
        "EE",
        "FI",
        "FR",
        "DE",
        "GR",
        "HU",
        "IE",
        "IT",
        "LV",
        "LT",
        "LU",
        "MT",
        "NL",
        "PL",
        "PT",
        "RO",
        "SK",
        "SI",
        "ES",
        "SE",
    }
)


def shipping_region_for_country(country_code: str) -> str | None:
    """Map a normalized destination country to the platform's bounded region set."""
    return "EU" if country_code in _EU_COUNTRIES else None


def shipping_treatment(
    item_subtotal_minor: int, charged_shipping_minor: int, allowed_shipping_minor: int
) -> dict[str, int]:
    """Compute the anti-abuse shipping split using only server-controlled input."""
    if item_subtotal_minor <= 0 or charged_shipping_minor < 0 or allowed_shipping_minor < 0:
        raise MarketplaceError("Invalid order or shipping amount")
    shipping_pass_through_minor = min(charged_shipping_minor, allowed_shipping_minor)
    shipping_excess_minor = max(charged_shipping_minor - allowed_shipping_minor, 0)
    commissionable_base_minor = item_subtotal_minor + shipping_excess_minor
    return {
        "item_subtotal_minor": item_subtotal_minor,
        "shipping_charged_minor": charged_shipping_minor,
        "shipping_allowance_minor": allowed_shipping_minor,
        "shipping_pass_through_minor": shipping_pass_through_minor,
        "shipping_excess_minor": shipping_excess_minor,
        "commissionable_base_minor": commissionable_base_minor,
        "total_paid_minor": item_subtotal_minor + charged_shipping_minor,
    }


async def shipping_allowance_for(
    db: AsyncSession, destination_country_code: str, currency: str
) -> MarketplaceShippingAllowance:
    """Resolve the allowance from platform configuration, never customer/creator payloads."""
    country = destination_country_code.upper().strip()
    if len(country) != 2 or not country.isalpha():
        raise MarketplaceError("Destination country must be an ISO alpha-2 code")
    currency = currency_code(currency)
    allowance = await db.scalar(
        select(MarketplaceShippingAllowance).where(
            MarketplaceShippingAllowance.scope == ShippingAllowanceScope.country,
            MarketplaceShippingAllowance.destination_code == country,
            MarketplaceShippingAllowance.currency == currency,
            MarketplaceShippingAllowance.active.is_(True),
        )
    )
    if allowance:
        return allowance
    region = shipping_region_for_country(country)
    if region:
        allowance = await db.scalar(
            select(MarketplaceShippingAllowance).where(
                MarketplaceShippingAllowance.scope == ShippingAllowanceScope.region,
                MarketplaceShippingAllowance.destination_code == region,
                MarketplaceShippingAllowance.currency == currency,
                MarketplaceShippingAllowance.active.is_(True),
            )
        )
    if not allowance:
        raise MarketplaceError("Shipping is not configured for this destination")
    return allowance


async def _allocation_amounts(
    db: AsyncSession, creator_id: UUID, currency: str, creator_pool_minor: int, event_at: datetime
) -> tuple[int, int]:
    _, metadata = await creator_revenue_allocation(
        db, creator_id, currency, creator_pool_minor, event_at
    )
    return int(metadata["creator_amount_minor"]), int(metadata["group_amount_minor"])


async def initiate_order(
    db: AsyncSession,
    buyer: User,
    listing_id: UUID,
    quantity: int,
    destination_country_code: str,
    idempotency_key: str,
) -> MarketplaceOrder:
    """Reserve stock and snapshot server-owned pricing and shipping treatment."""
    if not idempotency_key or len(idempotency_key) > 128:
        raise MarketplaceError("A valid Idempotency-Key is required")
    if quantity <= 0:
        raise MarketplaceError("Quantity must be positive")
    listing = await db.scalar(
        select(MarketplaceListing).where(MarketplaceListing.id == listing_id).with_for_update()
    )
    if not listing or listing.status is not MarketplaceListingStatus.published:
        raise MarketplaceError("Marketplace listing is not available")
    if listing.moderation_status is not ModerationStatus.approved:
        raise MarketplaceError("Marketplace listing is not approved")
    if listing.quantity_available < quantity:
        raise MarketplaceError("Marketplace listing is sold out")
    seller = await db.scalar(
        select(CreatorProfile).where(CreatorProfile.id == listing.owner_creator_id)
    )
    if not seller:
        raise MarketplaceError("Marketplace listing owner is unavailable")
    if await db.scalar(
        select(MarketplaceOrder)
        .join(PaymentAttempt)
        .where(
            PaymentAttempt.buyer_user_id == buyer.id,
            PaymentAttempt.idempotency_key == idempotency_key,
        )
    ):
        return await db.scalar(
            select(MarketplaceOrder)
            .join(PaymentAttempt)
            .where(
                PaymentAttempt.buyer_user_id == buyer.id,
                PaymentAttempt.idempotency_key == idempotency_key,
            )
        )  # type: ignore[return-value]
    if seller.user_id == buyer.id:
        raise MarketplaceError("Creators cannot purchase their own listing")
    currency = currency_code(listing.currency)
    allowance = await shipping_allowance_for(db, destination_country_code, currency)
    treatment = shipping_treatment(
        listing.price_amount_minor * quantity,
        listing.shipping_charged_minor,
        allowance.allowed_shipping_minor,
    )
    commission_bps = await commission_for(db, "marketplace")
    platform_fee, creator_pool = commission_amount(
        treatment["commissionable_base_minor"], commission_bps
    )
    now = datetime.now(UTC)
    creator_amount, group_amount = await _allocation_amounts(
        db, listing.owner_creator_id, currency, creator_pool, now
    )
    listing.quantity_available -= quantity
    attempt = PaymentAttempt(
        buyer_user_id=buyer.id,
        provider=get_settings().payment_provider,
        provider_reference=f"devpay_{secrets.token_urlsafe(18)}",
        amount_minor=treatment["total_paid_minor"],
        currency=currency,
        idempotency_key=idempotency_key,
    )
    db.add(attempt)
    await db.flush()
    order = MarketplaceOrder(
        public_id=f"ord_{secrets.token_urlsafe(12)}",
        listing_id=listing.id,
        buyer_user_id=buyer.id,
        seller_creator_id=listing.owner_creator_id,
        quantity=quantity,
        currency=currency,
        destination_country_code=destination_country_code.upper().strip(),
        **treatment,
        platform_fee_minor=platform_fee,
        creator_amount_minor=creator_amount,
        group_amount_minor=group_amount,
        commission_basis_points=commission_bps,
        reservation_expires_at=now + timedelta(minutes=15),
        payment_attempt_id=attempt.id,
    )
    db.add(order)
    await db.flush()
    await record_event(
        db,
        "marketplace.order_reserved",
        actor_user_id=buyer.id,
        target_type="marketplace_order",
        target_id=str(order.id),
        metadata={
            "listing_id": str(listing.id),
            "shipping_allowance_minor": str(order.shipping_allowance_minor),
            "commissionable_base_minor": str(order.commissionable_base_minor),
        },
    )
    return order


async def settle_order(db: AsyncSession, order: MarketplaceOrder) -> MarketplaceOrder:
    """Post a once-only, balanced order charge using its immutable shipping snapshot."""
    if order.status is not MarketplaceOrderStatus.awaiting_payment:
        return order
    attempt = await db.get(PaymentAttempt, order.payment_attempt_id)
    if not attempt or attempt.status is not PaymentStatus.succeeded:
        raise MarketplaceError("Marketplace order payment has not succeeded")
    event_at = attempt.completed_at or order.created_at
    fee, creator_pool = commission_amount(
        order.commissionable_base_minor, order.commission_basis_points
    )
    allocation_entries, allocation_metadata = await creator_revenue_allocation(
        db, order.seller_creator_id, order.currency, creator_pool, event_at
    )
    order.platform_fee_minor = fee
    order.creator_amount_minor = int(allocation_metadata["creator_amount_minor"])
    order.group_amount_minor = int(allocation_metadata["group_amount_minor"])
    clearing = await _account(db, LedgerAccountKind.platform_clearing, order.currency)
    revenue = await _account(db, LedgerAccountKind.platform_revenue, order.currency)
    entries = [
        (clearing, LedgerDirection.debit, order.total_paid_minor),
        (revenue, LedgerDirection.credit, order.platform_fee_minor),
        *allocation_entries,
    ]
    if order.shipping_pass_through_minor:
        shipping_recipient = await _account(
            db, LedgerAccountKind.creator_pending, order.currency, order.seller_creator_id
        )
        entries.append(
            (shipping_recipient, LedgerDirection.credit, order.shipping_pass_through_minor)
        )
    ledger = await post_entries(
        db,
        transaction_type=LedgerTransactionType.marketplace_order,
        currency=order.currency,
        idempotency_key=f"marketplace-order:{order.id}",
        reference=f"marketplace_order:{order.id}",
        entries=entries,
        metadata={
            "marketplace_order_id": str(order.id),
            "listing_id": str(order.listing_id),
            "item_subtotal_minor": str(order.item_subtotal_minor),
            "shipping_charged_minor": str(order.shipping_charged_minor),
            "shipping_allowance_minor": str(order.shipping_allowance_minor),
            "shipping_pass_through_minor": str(order.shipping_pass_through_minor),
            "shipping_excess_minor": str(order.shipping_excess_minor),
            "commissionable_base_minor": str(order.commissionable_base_minor),
            "platform_fee_minor": str(order.platform_fee_minor),
            "total_paid_minor": str(order.total_paid_minor),
            **allocation_metadata,
        },
    )
    order.status = MarketplaceOrderStatus.paid
    order.paid_at = event_at
    order.ledger_transaction_id = ledger.id
    await record_event(
        db,
        "marketplace.order_paid",
        actor_user_id=order.buyer_user_id,
        target_type="marketplace_order",
        target_id=str(order.id),
        metadata={"ledger_transaction_id": str(ledger.id)},
    )
    return order
