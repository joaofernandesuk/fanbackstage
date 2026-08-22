"""Server-authoritative physical marketplace checkout and settlement."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import case, func, select
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
    LedgerEntry,
    LedgerTransaction,
    LedgerTransactionType,
    PaymentAttempt,
    PaymentStatus,
)
from app.models.identity import User
from app.models.marketplace import (
    MarketplaceEarningsHoldPolicy,
    MarketplaceEarningsReleaseStatus,
    MarketplaceListing,
    MarketplaceListingMedia,
    MarketplaceListingStatus,
    MarketplaceOrder,
    MarketplaceOrderStatus,
    MarketplaceSellerRiskProfile,
    MarketplaceSellerTier,
    MarketplaceShippingAddress,
    MarketplaceShippingAllowance,
    MarketplaceTrackingEvent,
    ShippingAllowanceScope,
)


class MarketplaceError(ValueError):
    pass


async def seller_risk_profile(db: AsyncSession, creator_id: UUID) -> MarketplaceSellerRiskProfile:
    profile = await db.scalar(
        select(MarketplaceSellerRiskProfile)
        .where(MarketplaceSellerRiskProfile.creator_id == creator_id)
        .with_for_update()
    )
    if profile:
        return profile
    profile = MarketplaceSellerRiskProfile(creator_id=creator_id)
    db.add(profile)
    await db.flush()
    return profile


async def hold_policy_for_tier(
    db: AsyncSession, tier: MarketplaceSellerTier
) -> MarketplaceEarningsHoldPolicy:
    policy = await db.scalar(
        select(MarketplaceEarningsHoldPolicy).where(
            MarketplaceEarningsHoldPolicy.seller_tier == tier,
            MarketplaceEarningsHoldPolicy.active.is_(True),
        )
    )
    if policy:
        return policy
    policy = await db.scalar(
        select(MarketplaceEarningsHoldPolicy).where(
            MarketplaceEarningsHoldPolicy.active.is_(True),
            MarketplaceEarningsHoldPolicy.is_default.is_(True),
        )
    )
    if not policy:
        raise MarketplaceError("No active marketplace earnings hold policy is configured")
    return policy


def hold_policy_snapshot(policy: MarketplaceEarningsHoldPolicy) -> dict[str, object]:
    return {
        "id": str(policy.id),
        "seller_tier": policy.seller_tier.value,
        "hold_duration_seconds": policy.hold_duration_seconds,
        "active": policy.active,
        "is_default": policy.is_default,
    }


async def configure_hold_policy(
    db: AsyncSession,
    actor: User,
    *,
    tier_value: str,
    hold_duration_seconds: int,
    active: bool,
    is_default: bool,
) -> MarketplaceEarningsHoldPolicy:
    if hold_duration_seconds < 0:
        raise MarketplaceError("Marketplace hold duration must be nonnegative")
    try:
        tier = MarketplaceSellerTier(tier_value)
    except ValueError as exc:
        raise MarketplaceError("Marketplace seller tier is invalid") from exc
    policy = await db.scalar(
        select(MarketplaceEarningsHoldPolicy)
        .where(MarketplaceEarningsHoldPolicy.seller_tier == tier)
        .with_for_update()
    )
    old_value = hold_policy_snapshot(policy) if policy else None
    if is_default:
        defaults = (
            await db.scalars(
                select(MarketplaceEarningsHoldPolicy)
                .where(MarketplaceEarningsHoldPolicy.is_default.is_(True))
                .with_for_update()
            )
        ).all()
        for default in defaults:
            default.is_default = False
    if not policy:
        policy = MarketplaceEarningsHoldPolicy(
            seller_tier=tier,
            hold_duration_seconds=hold_duration_seconds,
            active=active,
            is_default=is_default,
        )
        db.add(policy)
        await db.flush()
        event_type = "marketplace.hold_policy_created"
    else:
        policy.hold_duration_seconds = hold_duration_seconds
        policy.active = active
        policy.is_default = is_default
        event_type = "marketplace.hold_policy_updated"
    await record_event(
        db,
        event_type,
        actor_user_id=actor.id,
        target_type="marketplace_earnings_hold_policy",
        target_id=str(policy.id),
        metadata={"old": old_value, "new": hold_policy_snapshot(policy)},
    )
    return policy


async def set_seller_tier(
    db: AsyncSession, actor: User, creator_id: UUID, tier_value: str, reason: str
) -> MarketplaceSellerRiskProfile:
    try:
        tier = MarketplaceSellerTier(tier_value)
    except ValueError as exc:
        raise MarketplaceError("Marketplace seller tier is invalid") from exc
    if not reason.strip():
        raise MarketplaceError("Seller tier change reason is required")
    profile = await seller_risk_profile(db, creator_id)
    previous = profile.tier
    profile.tier = tier
    await record_event(
        db,
        "marketplace.seller_tier_changed",
        actor_user_id=actor.id,
        target_type="creator_profile",
        target_id=str(creator_id),
        metadata={
            "previous_tier": previous.value,
            "new_tier": tier.value,
            "reason": reason.strip(),
        },
    )
    return profile


async def set_marketplace_suspension(
    db: AsyncSession, actor: User, creator_id: UUID, suspended: bool, reason: str
) -> MarketplaceSellerRiskProfile:
    if not reason.strip():
        raise MarketplaceError("Marketplace suspension reason is required")
    profile = await seller_risk_profile(db, creator_id)
    previous = profile.marketplace_suspended
    profile.marketplace_suspended = suspended
    await record_event(
        db,
        "marketplace.seller_suspension_changed",
        actor_user_id=actor.id,
        target_type="creator_profile",
        target_id=str(creator_id),
        metadata={"previous": previous, "suspended": suspended, "reason": reason.strip()},
    )
    return profile


def _country_code(value: str, field: str = "Country") -> str:
    normalized = value.upper().strip()
    if len(normalized) != 2 or not normalized.isalpha():
        raise MarketplaceError(f"{field} must be an ISO alpha-2 code")
    return normalized


async def create_listing(
    db: AsyncSession,
    actor: User,
    *,
    creator_id: UUID,
    title: str,
    description: str | None,
    category: str,
    condition: str,
    quantity_available: int,
    price_amount_minor: int,
    currency: str,
    shipping_mode: str,
    origin_country_code: str,
    shipping_charged_minor: int,
    media_asset_ids: list[UUID],
) -> MarketplaceListing:
    """Create a creator-owned physical listing; actor attribution never changes ownership."""
    from app.models.content import MediaAsset, MediaStatus
    from app.models.creator import CreatorProfile
    from app.models.marketplace import MarketplaceCondition, MarketplaceShippingMode

    if not title.strip() or not category.strip():
        raise MarketplaceError("Listing title and category are required")
    if quantity_available < 0 or price_amount_minor <= 0 or shipping_charged_minor < 0:
        raise MarketplaceError("Listing stock, price, or shipping charge is invalid")
    if len(media_asset_ids) != len(set(media_asset_ids)) or len(media_asset_ids) > 12:
        raise MarketplaceError("Listing media must be unique and limited to 12 assets")
    creator = await db.get(CreatorProfile, creator_id)
    if not creator:
        raise MarketplaceError("Creator not found")
    if (await seller_risk_profile(db, creator_id)).marketplace_suspended:
        raise MarketplaceError("Marketplace selling is suspended for this creator")
    if actor.id != creator.user_id:
        from app.groups.service import has_delegated_permission
        from app.models.groups import GroupPermission

        if not await has_delegated_permission(
            db, actor.id, creator_id, GroupPermission.manage_marketplace
        ):
            raise PermissionError("Delegated marketplace permission denied")
    try:
        listing_condition = MarketplaceCondition(condition)
        listing_shipping_mode = MarketplaceShippingMode(shipping_mode)
    except ValueError as exc:
        raise MarketplaceError("Listing condition or shipping mode is invalid") from exc
    assets = []
    if media_asset_ids:
        assets = (
            await db.scalars(
                select(MediaAsset).where(
                    MediaAsset.id.in_(media_asset_ids),
                    MediaAsset.owner_creator_id == creator_id,
                    MediaAsset.status == MediaStatus.ready,
                    MediaAsset.moderation_status == ModerationStatus.approved,
                )
            )
        ).all()
        if len(assets) != len(media_asset_ids):
            raise MarketplaceError("Listing media must be approved creator-owned media")
    listing = MarketplaceListing(
        public_id=f"ml_{secrets.token_urlsafe(12)}",
        owner_creator_id=creator_id,
        created_by_user_id=actor.id,
        title=title.strip(),
        description=description.strip() if description else None,
        category=category.strip().lower(),
        condition=listing_condition,
        quantity_available=quantity_available,
        price_amount_minor=price_amount_minor,
        currency=currency_code(currency),
        shipping_mode=listing_shipping_mode,
        origin_country_code=_country_code(origin_country_code, "Origin country"),
        shipping_charged_minor=shipping_charged_minor,
    )
    db.add(listing)
    await db.flush()
    db.add_all(
        MarketplaceListingMedia(listing_id=listing.id, media_asset_id=asset_id, position=position)
        for position, asset_id in enumerate(media_asset_ids)
    )
    await record_event(
        db,
        "marketplace.listing_created",
        actor_user_id=actor.id,
        target_type="marketplace_listing",
        target_id=str(listing.id),
        metadata={"owner_creator_id": str(creator_id)},
    )
    return listing


async def submit_listing_for_review(
    db: AsyncSession, actor: User, listing_id: UUID, creator_id: UUID
) -> MarketplaceListing:
    listing = await db.scalar(
        select(MarketplaceListing)
        .where(
            MarketplaceListing.id == listing_id, MarketplaceListing.owner_creator_id == creator_id
        )
        .with_for_update()
    )
    if not listing:
        raise MarketplaceError("Marketplace listing not found")
    if listing.status not in {MarketplaceListingStatus.draft, MarketplaceListingStatus.paused}:
        raise MarketplaceError("Listing cannot be submitted for review")
    listing.status = MarketplaceListingStatus.pending_review
    listing.moderation_status = ModerationStatus.queued
    await record_event(
        db,
        "marketplace.listing_submitted",
        actor_user_id=actor.id,
        target_type="marketplace_listing",
        target_id=str(listing.id),
        metadata={"owner_creator_id": str(creator_id)},
    )
    return listing


async def decide_listing_moderation(
    db: AsyncSession, actor: User, listing_id: UUID, approved: bool
) -> MarketplaceListing:
    listing = await db.scalar(
        select(MarketplaceListing).where(MarketplaceListing.id == listing_id).with_for_update()
    )
    if not listing or listing.status is not MarketplaceListingStatus.pending_review:
        raise MarketplaceError("Marketplace listing is not awaiting review")
    listing.moderation_status = ModerationStatus.approved if approved else ModerationStatus.rejected
    listing.status = (
        MarketplaceListingStatus.published if approved else MarketplaceListingStatus.rejected
    )
    listing.published_at = datetime.now(UTC) if approved else None
    await record_event(
        db,
        "marketplace.listing_moderated",
        actor_user_id=actor.id,
        target_type="marketplace_listing",
        target_id=str(listing.id),
        metadata={"approved": approved, "owner_creator_id": str(listing.owner_creator_id)},
    )
    return listing


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
    db: AsyncSession,
    destination_country_code: str,
    currency: str,
    destination_region_code: str | None = None,
) -> MarketplaceShippingAllowance:
    """Resolve the allowance from platform configuration, never customer/creator payloads."""
    country = destination_country_code.upper().strip()
    if len(country) != 2 or not country.isalpha():
        raise MarketplaceError("Destination country must be an ISO alpha-2 code")
    currency = currency_code(currency)
    region = destination_region_code.upper().strip() if destination_region_code else None
    if region and (not region.isalnum() or len(region) > 16):
        raise MarketplaceError("Destination region is invalid")
    # Exact country + region is the highest authority.  Both values originate
    # from checkout address data, never from the creator's shipping charge.
    allowance = None
    if region:
        allowance = await db.scalar(
            select(MarketplaceShippingAllowance).where(
                MarketplaceShippingAllowance.scope == ShippingAllowanceScope.country_region,
                MarketplaceShippingAllowance.country_code == country,
                MarketplaceShippingAllowance.region_code == region,
                MarketplaceShippingAllowance.currency == currency,
                MarketplaceShippingAllowance.active.is_(True),
            )
        )
    if allowance:
        return allowance
    allowance = await db.scalar(
        select(MarketplaceShippingAllowance).where(
            MarketplaceShippingAllowance.scope == ShippingAllowanceScope.country,
            (
                (MarketplaceShippingAllowance.country_code == country)
                | (
                    MarketplaceShippingAllowance.country_code.is_(None)
                    & (MarketplaceShippingAllowance.destination_code == country)
                )
            ),
            MarketplaceShippingAllowance.currency == currency,
            MarketplaceShippingAllowance.active.is_(True),
        )
    )
    if allowance:
        return allowance
    allowance = await db.scalar(
        select(MarketplaceShippingAllowance).where(
            MarketplaceShippingAllowance.scope == ShippingAllowanceScope.global_,
            MarketplaceShippingAllowance.currency == currency,
            MarketplaceShippingAllowance.active.is_(True),
        )
    )
    if not allowance:
        raise MarketplaceError("Shipping is not configured for this destination")
    return allowance


def _allowance_scope(
    country_code: str | None, region_code: str | None
) -> tuple[ShippingAllowanceScope, str, str | None, str | None]:
    country = country_code.upper().strip() if country_code else None
    region = region_code.upper().strip() if region_code else None
    if country and (len(country) != 2 or not country.isalpha()):
        raise MarketplaceError("Allowance country must be an ISO alpha-2 code")
    if region and (not region.isalnum() or len(region) > 16):
        raise MarketplaceError("Allowance region is invalid")
    if region and not country:
        raise MarketplaceError("A regional allowance requires a destination country")
    if country and region:
        return ShippingAllowanceScope.country_region, f"{country}:{region}", country, region
    if country:
        return ShippingAllowanceScope.country, country, country, None
    return ShippingAllowanceScope.global_, "*", None, None


def allowance_snapshot(allowance: MarketplaceShippingAllowance) -> dict[str, object]:
    return {
        "id": str(allowance.id),
        "scope": allowance.scope.value,
        "country_code": allowance.country_code,
        "region_code": allowance.region_code,
        "currency": allowance.currency,
        "allowed_shipping_minor": allowance.allowed_shipping_minor,
        "active": allowance.active,
    }


async def configure_shipping_allowance(
    db: AsyncSession,
    actor: User,
    *,
    country_code: str | None,
    region_code: str | None,
    currency: str,
    allowed_shipping_minor: int,
    active: bool = True,
) -> MarketplaceShippingAllowance:
    """Create or edit an allowance as an auditable platform-admin action."""
    if allowed_shipping_minor < 0:
        raise MarketplaceError("Shipping allowance must be nonnegative")
    scope, destination_code, normalized_country, normalized_region = _allowance_scope(
        country_code, region_code
    )
    currency = currency_code(currency)
    allowance = await db.scalar(
        select(MarketplaceShippingAllowance)
        .where(
            MarketplaceShippingAllowance.scope == scope,
            MarketplaceShippingAllowance.destination_code == destination_code,
            MarketplaceShippingAllowance.currency == currency,
        )
        .with_for_update()
    )
    old_value = allowance_snapshot(allowance) if allowance else None
    if not allowance:
        allowance = MarketplaceShippingAllowance(
            scope=scope,
            destination_code=destination_code,
            country_code=normalized_country,
            region_code=normalized_region,
            currency=currency,
            allowed_shipping_minor=allowed_shipping_minor,
            active=active,
        )
        db.add(allowance)
        await db.flush()
        event_type = "marketplace.shipping_allowance_created"
    else:
        allowance.country_code = normalized_country
        allowance.region_code = normalized_region
        allowance.allowed_shipping_minor = allowed_shipping_minor
        allowance.active = active
        event_type = "marketplace.shipping_allowance_updated"
    await record_event(
        db,
        event_type,
        actor_user_id=actor.id,
        target_type="marketplace_shipping_allowance",
        target_id=str(allowance.id),
        metadata={
            "old": old_value,
            "new": allowance_snapshot(allowance),
            "scope": allowance.scope.value,
        },
    )
    return allowance


async def disable_shipping_allowance(
    db: AsyncSession, actor: User, allowance_id: UUID
) -> MarketplaceShippingAllowance:
    """Safely retire configuration without deleting any historical reference."""
    allowance = await db.scalar(
        select(MarketplaceShippingAllowance)
        .where(MarketplaceShippingAllowance.id == allowance_id)
        .with_for_update()
    )
    if not allowance:
        raise MarketplaceError("Shipping allowance not found")
    old_value = allowance_snapshot(allowance)
    allowance.active = False
    await record_event(
        db,
        "marketplace.shipping_allowance_disabled",
        actor_user_id=actor.id,
        target_type="marketplace_shipping_allowance",
        target_id=str(allowance.id),
        metadata={
            "old": old_value,
            "new": allowance_snapshot(allowance),
            "scope": allowance.scope.value,
        },
    )
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
    destination_region_code: str | None = None,
    shipping_address: dict[str, str | None] | None = None,
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
    if (await seller_risk_profile(db, listing.owner_creator_id)).marketplace_suspended:
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
    allowance = await shipping_allowance_for(
        db, destination_country_code, currency, destination_region_code
    )
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
    if shipping_address:
        address_country = _country_code(
            shipping_address["country_code"], "Shipping address country"
        )
        if address_country != order.destination_country_code:
            raise MarketplaceError("Shipping address country must match checkout destination")
        address_region = shipping_address.get("region_code")
        if (
            destination_region_code
            and address_region
            and (address_region.upper().strip() != destination_region_code.upper().strip())
        ):
            raise MarketplaceError("Shipping address region must match checkout destination")
        db.add(
            MarketplaceShippingAddress(
                order_id=order.id,
                recipient_name=str(shipping_address["recipient_name"]).strip(),
                line1=str(shipping_address["line1"]).strip(),
                line2=(
                    str(shipping_address["line2"]).strip()
                    if shipping_address.get("line2")
                    else None
                ),
                city=str(shipping_address["city"]).strip(),
                region_code=address_region.upper().strip() if address_region else None,
                postal_code=str(shipping_address["postal_code"]).strip(),
                country_code=address_country,
            )
        )
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


async def shipping_address_for_order(
    db: AsyncSession, order_id: UUID, actor: User
) -> MarketplaceShippingAddress:
    """Return restricted address only to buyer, seller/order manager, or platform admin."""
    order = await db.get(MarketplaceOrder, order_id)
    if not order:
        raise MarketplaceError("Marketplace order not found")
    is_buyer = order.buyer_user_id == actor.id
    from app.groups.service import has_delegated_permission
    from app.models.creator import CreatorProfile
    from app.models.groups import GroupPermission

    seller = await db.scalar(
        select(CreatorProfile).where(
            CreatorProfile.id == order.seller_creator_id, CreatorProfile.user_id == actor.id
        )
    )
    delegated = await has_delegated_permission(
        db, actor.id, order.seller_creator_id, GroupPermission.manage_marketplace_orders
    )
    is_admin = any(role.name in {"admin", "super_admin"} for role in actor.roles)
    if not (is_buyer or seller or delegated or is_admin):
        raise PermissionError("Marketplace shipping address permission denied")
    if (seller or delegated) and order.status is MarketplaceOrderStatus.awaiting_payment:
        raise PermissionError("Shipping address is unavailable until payment succeeds")
    address = await db.scalar(
        select(MarketplaceShippingAddress).where(MarketplaceShippingAddress.order_id == order.id)
    )
    if not address:
        raise MarketplaceError("Marketplace shipping address is not available")
    await record_event(
        db,
        "marketplace.shipping_address_accessed",
        actor_user_id=actor.id,
        target_type="marketplace_order",
        target_id=str(order.id),
        metadata={"access_kind": "buyer" if is_buyer else "seller_or_support"},
    )
    return address


async def release_order_reservation(
    db: AsyncSession, order_id: UUID, reason: str
) -> MarketplaceOrder:
    """Restore reserved stock once for an unconfirmed marketplace payment."""
    order = await db.scalar(
        select(MarketplaceOrder).where(MarketplaceOrder.id == order_id).with_for_update()
    )
    if not order:
        raise MarketplaceError("Marketplace order not found")
    if order.status is not MarketplaceOrderStatus.awaiting_payment:
        return order
    listing = await db.scalar(
        select(MarketplaceListing)
        .where(MarketplaceListing.id == order.listing_id)
        .with_for_update()
    )
    if not listing:
        raise MarketplaceError("Marketplace listing is unavailable")
    listing.quantity_available += order.quantity
    order.status = MarketplaceOrderStatus.cancelled
    attempt = await db.get(PaymentAttempt, order.payment_attempt_id)
    if attempt and attempt.status is PaymentStatus.pending:
        attempt.status = PaymentStatus.failed
    await record_event(
        db,
        "marketplace.order_reservation_released",
        actor_user_id=order.buyer_user_id,
        target_type="marketplace_order",
        target_id=str(order.id),
        metadata={"reason": reason},
    )
    return order


async def expire_marketplace_reservations(db: AsyncSession, limit: int = 100) -> int:
    """Durably cancel expired unconfirmed orders and restore stock under row locks."""
    rows = (
        await db.scalars(
            select(MarketplaceOrder)
            .where(
                MarketplaceOrder.status == MarketplaceOrderStatus.awaiting_payment,
                MarketplaceOrder.reservation_expires_at <= datetime.now(UTC),
            )
            .order_by(MarketplaceOrder.reservation_expires_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    released = 0
    for order in rows:
        if (
            await release_order_reservation(db, order.id, "payment_reservation_expired")
        ).status is (MarketplaceOrderStatus.cancelled):
            released += 1
    return released


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


async def mark_order_processing(
    db: AsyncSession, order_id: UUID, actor: User, creator_id: UUID
) -> MarketplaceOrder:
    order = await db.scalar(
        select(MarketplaceOrder)
        .where(MarketplaceOrder.id == order_id, MarketplaceOrder.seller_creator_id == creator_id)
        .with_for_update()
    )
    if not order or order.status is not MarketplaceOrderStatus.paid:
        raise MarketplaceError("Order cannot be marked as processing")
    order.status = MarketplaceOrderStatus.processing
    await record_event(
        db,
        "marketplace.order_processing",
        actor_user_id=actor.id,
        target_type="marketplace_order",
        target_id=str(order.id),
    )
    return order


async def mark_order_shipped(
    db: AsyncSession,
    order_id: UUID,
    actor: User,
    creator_id: UUID,
    carrier: str | None,
    tracking_reference: str | None,
) -> MarketplaceOrder:
    order = await db.scalar(
        select(MarketplaceOrder)
        .where(MarketplaceOrder.id == order_id, MarketplaceOrder.seller_creator_id == creator_id)
        .with_for_update()
    )
    if not order or order.status not in {
        MarketplaceOrderStatus.paid,
        MarketplaceOrderStatus.processing,
    }:
        raise MarketplaceError("Order cannot be marked as shipped")
    order.status = MarketplaceOrderStatus.shipped
    order.shipped_at = datetime.now(UTC)
    order.carrier = carrier.strip() if carrier else None
    order.tracking_reference = tracking_reference.strip() if tracking_reference else None
    db.add(
        MarketplaceTrackingEvent(
            order_id=order.id,
            event_type="shipment_created",
            carrier=order.carrier,
            tracking_reference=order.tracking_reference,
        )
    )
    await record_event(
        db,
        "marketplace.order_shipped",
        actor_user_id=actor.id,
        target_type="marketplace_order",
        target_id=str(order.id),
        metadata={
            "carrier": order.carrier,
            "has_tracking_reference": bool(order.tracking_reference),
        },
    )
    return order


async def confirm_order_delivery(db: AsyncSession, order_id: UUID, buyer: User) -> MarketplaceOrder:
    """Buyer confirmation is the Phase 9 authoritative delivery signal."""
    order = await db.scalar(
        select(MarketplaceOrder)
        .where(MarketplaceOrder.id == order_id, MarketplaceOrder.buyer_user_id == buyer.id)
        .with_for_update()
    )
    if not order or order.status is not MarketplaceOrderStatus.shipped:
        raise MarketplaceError("Order cannot be marked as delivered")
    profile = await seller_risk_profile(db, order.seller_creator_id)
    policy = await hold_policy_for_tier(db, profile.tier)
    now = datetime.now(UTC)
    order.status = MarketplaceOrderStatus.delivered
    order.delivered_at = now
    order.seller_tier_snapshot = profile.tier
    order.hold_duration_seconds_snapshot = policy.hold_duration_seconds
    order.earnings_hold_until = now + timedelta(seconds=policy.hold_duration_seconds)
    order.earnings_release_status = MarketplaceEarningsReleaseStatus.pending
    order.release_block_reason = None
    db.add(
        MarketplaceTrackingEvent(
            order_id=order.id,
            event_type="delivery_confirmed_by_buyer",
            carrier=order.carrier,
            tracking_reference=order.tracking_reference,
        )
    )
    await record_event(
        db,
        "marketplace.order_delivered",
        actor_user_id=buyer.id,
        target_type="marketplace_order",
        target_id=str(order.id),
        metadata={
            "seller_tier": profile.tier.value,
            "hold_duration_seconds": policy.hold_duration_seconds,
        },
    )
    return order


async def release_order_earnings(db: AsyncSession, order: MarketplaceOrder) -> bool:
    """Move exactly this order's historical pending allocations once delivery hold expires."""
    order = await db.scalar(
        select(MarketplaceOrder).where(MarketplaceOrder.id == order.id).with_for_update()
    )
    assert order
    now = datetime.now(UTC)
    if order.earnings_release_status is MarketplaceEarningsReleaseStatus.released:
        return False
    if (
        order.status is not MarketplaceOrderStatus.delivered
        or not order.earnings_hold_until
        or order.earnings_hold_until > now
    ):
        return False
    if not order.ledger_transaction_id:
        raise MarketplaceError("Order settlement ledger is missing")
    original = await db.get(LedgerTransaction, order.ledger_transaction_id)
    assert original
    creator_pending = await _account(
        db, LedgerAccountKind.creator_pending, order.currency, order.seller_creator_id
    )
    creator_available = await _account(
        db, LedgerAccountKind.creator_available, order.currency, order.seller_creator_id
    )
    creator_amount = int(original.metadata_json["creator_amount_minor"]) + int(
        original.metadata_json.get("shipping_pass_through_minor", 0)
    )
    entries = [
        (creator_pending, LedgerDirection.debit, creator_amount),
        (creator_available, LedgerDirection.credit, creator_amount),
    ]
    group_amount = int(original.metadata_json.get("group_amount_minor", 0))
    if group_amount:
        group_id = original.metadata_json.get("group_id")
        if not group_id:
            raise MarketplaceError("Order group allocation snapshot is incomplete")
        group_pending = await _account(
            db, LedgerAccountKind.group_pending, order.currency, owner_group_id=UUID(group_id)
        )
        group_available = await _account(
            db, LedgerAccountKind.group_available, order.currency, owner_group_id=UUID(group_id)
        )
        entries.extend(
            [
                (group_pending, LedgerDirection.debit, group_amount),
                (group_available, LedgerDirection.credit, group_amount),
            ]
        )
    transaction = await post_entries(
        db,
        transaction_type=LedgerTransactionType.earnings_release,
        currency=order.currency,
        idempotency_key=f"marketplace-release:{order.id}",
        reference=f"marketplace_release:{order.id}",
        entries=entries,
        metadata={
            "marketplace_order_id": str(order.id),
            "original_ledger_transaction_id": str(original.id),
            "seller_tier_snapshot": order.seller_tier_snapshot.value
            if order.seller_tier_snapshot
            else "",
            "hold_duration_seconds_snapshot": str(order.hold_duration_seconds_snapshot or 0),
            "creator_amount_minor": str(creator_amount),
            "group_amount_minor": str(group_amount),
        },
    )
    order.earnings_release_status = MarketplaceEarningsReleaseStatus.released
    order.earnings_released_at = now
    await record_event(
        db,
        "marketplace.order_earnings_released",
        target_type="marketplace_order",
        target_id=str(order.id),
        metadata={"ledger_transaction_id": str(transaction.id)},
    )
    return True


async def release_eligible_marketplace_earnings(db: AsyncSession, limit: int = 100) -> int:
    """Database-backed replay-safe worker query; no process-local delivery timer exists."""
    rows = (
        await db.scalars(
            select(MarketplaceOrder)
            .where(
                MarketplaceOrder.status == MarketplaceOrderStatus.delivered,
                MarketplaceOrder.earnings_release_status
                == MarketplaceEarningsReleaseStatus.pending,
                MarketplaceOrder.earnings_hold_until <= datetime.now(UTC),
            )
            .order_by(MarketplaceOrder.earnings_hold_until)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    released = 0
    for order in rows:
        if await release_order_earnings(db, order):
            released += 1
    return released


async def _account_balance(db: AsyncSession, account_id: UUID) -> int:
    value = await db.scalar(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (LedgerEntry.direction == LedgerDirection.credit, LedgerEntry.amount_minor),
                        else_=-LedgerEntry.amount_minor,
                    )
                ),
                0,
            )
        ).where(LedgerEntry.ledger_account_id == account_id)
    )
    return int(value or 0)


async def _reverse_order_allocation(
    db: AsyncSession,
    order: MarketplaceOrder,
    *,
    transaction_type: LedgerTransactionType,
    reason: str,
) -> None:
    if not order.ledger_transaction_id:
        raise MarketplaceError("Only settled marketplace orders can be reversed")
    original = await db.get(LedgerTransaction, order.ledger_transaction_id)
    assert original
    creator_amount = int(original.metadata_json["creator_amount_minor"]) + int(
        original.metadata_json.get("shipping_pass_through_minor", 0)
    )
    group_amount = int(original.metadata_json.get("group_amount_minor", 0))
    clearing = await _account(db, LedgerAccountKind.platform_clearing, order.currency)
    revenue = await _account(db, LedgerAccountKind.platform_revenue, order.currency)
    creator_pending = await _account(
        db, LedgerAccountKind.creator_pending, order.currency, order.seller_creator_id
    )
    creator_available = await _account(
        db, LedgerAccountKind.creator_available, order.currency, order.seller_creator_id
    )
    pending_reversal = min(max(await _account_balance(db, creator_pending.id), 0), creator_amount)
    entries = [
        (clearing, LedgerDirection.credit, order.total_paid_minor),
        (revenue, LedgerDirection.debit, order.platform_fee_minor),
    ]
    if pending_reversal:
        entries.append((creator_pending, LedgerDirection.debit, pending_reversal))
    if creator_amount - pending_reversal:
        entries.append(
            (creator_available, LedgerDirection.debit, creator_amount - pending_reversal)
        )
    if group_amount:
        group_id = original.metadata_json.get("group_id")
        if not group_id:
            raise MarketplaceError("Marketplace group allocation snapshot is incomplete")
        group_pending = await _account(
            db, LedgerAccountKind.group_pending, order.currency, owner_group_id=UUID(group_id)
        )
        group_available = await _account(
            db, LedgerAccountKind.group_available, order.currency, owner_group_id=UUID(group_id)
        )
        group_pending_reversal = min(
            max(await _account_balance(db, group_pending.id), 0), group_amount
        )
        if group_pending_reversal:
            entries.append((group_pending, LedgerDirection.debit, group_pending_reversal))
        if group_amount - group_pending_reversal:
            entries.append(
                (group_available, LedgerDirection.debit, group_amount - group_pending_reversal)
            )
    await post_entries(
        db,
        transaction_type=transaction_type,
        currency=order.currency,
        idempotency_key=f"marketplace-{transaction_type.value}:{order.id}",
        reference=f"marketplace_{transaction_type.value}:{order.id}",
        reversal_of_transaction_id=original.id,
        entries=entries,
        metadata={
            "marketplace_order_id": str(order.id),
            "reason": reason,
            "original_ledger_transaction_id": str(original.id),
            "original_group_contract_id": original.metadata_json.get("group_contract_id", ""),
            "creator_amount_minor": str(creator_amount),
            "group_amount_minor": str(group_amount),
        },
    )


async def refund_order(
    db: AsyncSession, order_id: UUID, actor: User | None, reason: str
) -> MarketplaceOrder:
    order = await db.scalar(
        select(MarketplaceOrder).where(MarketplaceOrder.id == order_id).with_for_update()
    )
    if not order:
        raise MarketplaceError("Marketplace order not found")
    if order.status is MarketplaceOrderStatus.refunded:
        return order
    if order.status in {MarketplaceOrderStatus.awaiting_payment, MarketplaceOrderStatus.cancelled}:
        raise MarketplaceError("Unsettled marketplace order cannot be refunded")
    if order.status is MarketplaceOrderStatus.chargeback:
        raise MarketplaceError("Chargeback marketplace order cannot be refunded")
    # A dispute intentionally obscures the operational status while it is open.
    # Shipment is the durable boundary for physical stock: a refund of an order
    # that has never shipped restores the original units exactly once.
    restore_stock = order.shipped_at is None
    await _reverse_order_allocation(
        db, order, transaction_type=LedgerTransactionType.refund, reason=reason
    )
    if restore_stock:
        listing = await db.scalar(
            select(MarketplaceListing)
            .where(MarketplaceListing.id == order.listing_id)
            .with_for_update()
        )
        assert listing
        listing.quantity_available += order.quantity
    order.status = MarketplaceOrderStatus.refunded
    order.earnings_release_status = MarketplaceEarningsReleaseStatus.blocked
    order.release_block_reason = "refunded"
    attempt = await db.get(PaymentAttempt, order.payment_attempt_id)
    if attempt:
        attempt.status = PaymentStatus.refunded
    await record_event(
        db,
        "marketplace.order_refunded",
        actor_user_id=actor.id if actor else None,
        target_type="marketplace_order",
        target_id=str(order.id),
        metadata={"reason": reason, "stock_restored": restore_stock},
    )
    return order


async def cancel_order(
    db: AsyncSession, order_id: UUID, actor: User, creator_id: UUID, reason: str
) -> MarketplaceOrder:
    """Seller cancellation is permitted only before the immutable shipment event."""
    order = await db.scalar(
        select(MarketplaceOrder)
        .where(
            MarketplaceOrder.id == order_id,
            MarketplaceOrder.seller_creator_id == creator_id,
        )
        .with_for_update()
    )
    if not order or order.status not in {MarketplaceOrderStatus.paid, MarketplaceOrderStatus.processing}:
        raise MarketplaceError("Only an unshipped paid order can be cancelled")
    return await refund_order(db, order.id, actor, reason)


async def open_order_dispute(
    db: AsyncSession, order_id: UUID, buyer: User, reason: str
) -> MarketplaceOrder:
    order = await db.scalar(
        select(MarketplaceOrder)
        .where(MarketplaceOrder.id == order_id, MarketplaceOrder.buyer_user_id == buyer.id)
        .with_for_update()
    )
    if not order or order.status not in {
        MarketplaceOrderStatus.paid,
        MarketplaceOrderStatus.processing,
        MarketplaceOrderStatus.shipped,
        MarketplaceOrderStatus.delivered,
    }:
        raise MarketplaceError("Marketplace order cannot be disputed")
    return await block_order_for_dispute(db, order, buyer.id, reason)


async def block_order_for_dispute(
    db: AsyncSession,
    order: MarketplaceOrder,
    actor_user_id: UUID | None,
    reason: str,
) -> MarketplaceOrder:
    """Persist a provider or buyer dispute as an earnings-release blocker."""
    if order.status in {MarketplaceOrderStatus.refunded, MarketplaceOrderStatus.chargeback}:
        return order
    if order.status is MarketplaceOrderStatus.disputed:
        return order
    if order.status not in {
        MarketplaceOrderStatus.paid,
        MarketplaceOrderStatus.processing,
        MarketplaceOrderStatus.shipped,
        MarketplaceOrderStatus.delivered,
    }:
        raise MarketplaceError("Marketplace order cannot be disputed")
    order.status = MarketplaceOrderStatus.disputed
    order.earnings_release_status = MarketplaceEarningsReleaseStatus.blocked
    order.release_block_reason = "unresolved_dispute"
    await record_event(
        db,
        "marketplace.order_disputed",
        actor_user_id=actor_user_id,
        target_type="marketplace_order",
        target_id=str(order.id),
        metadata={"reason": reason},
    )
    return order


async def resolve_order_dispute(
    db: AsyncSession, order_id: UUID, actor: User | None, refund: bool, reason: str
) -> MarketplaceOrder:
    order = await db.scalar(
        select(MarketplaceOrder).where(MarketplaceOrder.id == order_id).with_for_update()
    )
    if not order or order.status is not MarketplaceOrderStatus.disputed:
        raise MarketplaceError("Marketplace dispute is not open")
    if refund:
        return await refund_order(db, order.id, actor, reason)
    # The timestamps are append-only fulfilment facts, so they safely restore
    # the eligible operational state without trusting a caller-supplied state.
    order.status = (
        MarketplaceOrderStatus.delivered if order.delivered_at else MarketplaceOrderStatus.shipped
    )
    order.earnings_release_status = MarketplaceEarningsReleaseStatus.pending
    order.release_block_reason = None
    await record_event(
        db,
        "marketplace.dispute_resolved",
        actor_user_id=actor.id if actor else None,
        target_type="marketplace_order",
        target_id=str(order.id),
        metadata={"resolution": "seller_favour", "reason": reason},
    )
    return order


async def chargeback_order(
    db: AsyncSession, order_id: UUID, actor: User | None, reason: str
) -> MarketplaceOrder:
    order = await db.scalar(
        select(MarketplaceOrder).where(MarketplaceOrder.id == order_id).with_for_update()
    )
    if not order:
        raise MarketplaceError("Marketplace order not found")
    if order.status is MarketplaceOrderStatus.chargeback:
        return order
    if order.status is MarketplaceOrderStatus.refunded:
        raise MarketplaceError("Refunded marketplace order cannot be charged back")
    await _reverse_order_allocation(
        db, order, transaction_type=LedgerTransactionType.chargeback, reason=reason
    )
    order.status = MarketplaceOrderStatus.chargeback
    order.earnings_release_status = MarketplaceEarningsReleaseStatus.blocked
    order.release_block_reason = "chargeback"
    attempt = await db.get(PaymentAttempt, order.payment_attempt_id)
    if attempt:
        attempt.status = PaymentStatus.chargeback
    await record_event(
        db,
        "marketplace.order_chargeback",
        actor_user_id=actor.id if actor else None,
        target_type="marketplace_order",
        target_id=str(order.id),
        metadata={"reason": reason},
    )
    return order
