"""Server-authoritative physical marketplace checkout and settlement."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import case, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.audit.service import record_event
from app.compliance.policy import resolve_compliance_decision
from app.compliance.types import (
    ComplianceAccessError,
    ComplianceDecision,
    require_compliance_access,
)
from app.core.config import get_settings
from app.creators.service import (
    require_public_creator_access,
    resolve_creator_compliance_eligibility,
)
from app.finance.providers import new_provider_reference
from app.finance.service import (
    _account,
    commission_amount,
    commission_for,
    creator_revenue_allocation,
    currency_code,
    lock_payment_idempotency,
    post_entries,
)
from app.media.contexts import require_media_context_available
from app.models.audit import AuditEvent
from app.models.compliance import ComplianceFeature
from app.models.content import (
    DerivativeType,
    Gallery,
    GalleryItem,
    MediaAsset,
    MediaAudience,
    MediaDerivative,
    MediaStatus,
    ModerationStatus,
    VideoContent,
)
from app.models.creator import CreatorProfile, CreatorStatus
from app.models.finance import (
    ExcessCaptureSource,
    LedgerAccount,
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
from app.notifications.service import emit_transactional


class MarketplaceError(ValueError):
    def __init__(self, message: str, compliance_decision: ComplianceDecision | None = None):
        super().__init__(message)
        self.compliance_decision = compliance_decision
        self.code = compliance_decision.code if compliance_decision else None
        self.action = compliance_decision.action if compliance_decision else None


async def require_marketplace_purchase_compliance(
    db: AsyncSession,
    buyer: User,
    decisions: dict[ComplianceFeature, ComplianceDecision] | None = None,
) -> None:
    for feature in (ComplianceFeature.marketplace, ComplianceFeature.purchases):
        decision = (
            decisions.get(feature)
            if decisions
            else await resolve_compliance_decision(
                db,
                user=buyer,
                feature=feature,
                adult_restricted=False,
            )
        )
        if decision is None:
            raise MarketplaceError("Marketplace compliance decision is unavailable")
        try:
            require_compliance_access(decision)
        except ComplianceAccessError as exc:
            raise MarketplaceError(exc.decision.reason, exc.decision) from exc


class MarketplaceTerminalPaymentError(MarketplaceError):
    """The canonical idempotent order reached a terminal payment failure."""

    def __init__(self, order: MarketplaceOrder):
        super().__init__("Marketplace order payment failed; retry with a new Idempotency-Key")
        self.order_id = order.id
        self.order_status = order.status


def listing_media_exclusivity_predicates(
    asset_id: ColumnElement[UUID],
) -> tuple[ColumnElement[bool], ...]:
    """Fail closed when marketplace media is also attached to another domain.

    Public marketplace display derivatives are intentionally acquisition-safe.
    Reusing a content, Story, feed, or message asset would let a public listing
    become an alternate delivery path around that domain's access policy.
    """
    from app.models.messaging import MessageAttachment
    from app.models.social import FeedPostMedia
    from app.models.story import Story

    return (
        ~exists(select(GalleryItem.id).where(GalleryItem.media_asset_id == asset_id)),
        ~exists(select(Gallery.id).where(Gallery.cover_media_asset_id == asset_id)),
        ~exists(select(VideoContent.id).where(VideoContent.source_media_asset_id == asset_id)),
        ~exists(select(Story.id).where(Story.media_asset_id == asset_id)),
        ~exists(select(FeedPostMedia.id).where(FeedPostMedia.media_asset_id == asset_id)),
        ~exists(select(MessageAttachment.id).where(MessageAttachment.media_asset_id == asset_id)),
    )


async def listing_media_is_public_exclusive(db: AsyncSession, asset_id: UUID) -> bool:
    """Return whether an asset is dedicated to marketplace public display."""
    return bool(
        await db.scalar(
            select(MediaAsset.id).where(
                MediaAsset.id == asset_id,
                *listing_media_exclusivity_predicates(MediaAsset.id),
            )
        )
    )


async def public_listing_media(
    db: AsyncSession, listing: MarketplaceListing
) -> list[tuple[MarketplaceListingMedia, MediaDerivative]]:
    """Return only safe, approved display derivatives for a public listing."""

    if (
        listing.status is not MarketplaceListingStatus.published
        or listing.moderation_status is not ModerationStatus.approved
    ):
        return []
    rows = await db.execute(
        select(MarketplaceListingMedia, MediaDerivative)
        .join(MediaAsset, MediaAsset.id == MarketplaceListingMedia.media_asset_id)
        .join(MediaDerivative, MediaDerivative.media_asset_id == MediaAsset.id)
        .where(
            MarketplaceListingMedia.listing_id == listing.id,
            MediaAsset.owner_creator_id == listing.owner_creator_id,
            MediaAsset.status == MediaStatus.ready,
            MediaAsset.deleted_at.is_(None),
            MediaAsset.moderation_status == ModerationStatus.approved,
            MediaAsset.audience == MediaAudience.safe_public,
            *listing_media_exclusivity_predicates(MediaAsset.id),
            MediaDerivative.derivative_type == DerivativeType.display,
            MediaDerivative.status == MediaStatus.ready,
        )
        .order_by(MarketplaceListingMedia.position, MediaDerivative.created_at)
    )
    return list(rows.all())


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
    from app.models.marketplace import MarketplaceCondition, MarketplaceShippingMode

    if not title.strip() or not category.strip():
        raise MarketplaceError("Listing title and category are required")
    if quantity_available < 0 or price_amount_minor <= 0 or shipping_charged_minor < 0:
        raise MarketplaceError("Listing stock, price, or shipping charge is invalid")
    if len(media_asset_ids) != len(set(media_asset_ids)) or len(media_asset_ids) > 12:
        raise MarketplaceError("Listing media must be unique and limited to 12 assets")
    creator = await db.get(CreatorProfile, creator_id)
    if not creator or creator.status is not CreatorStatus.approved:
        raise MarketplaceError("Creator not found")
    if not (await resolve_creator_compliance_eligibility(db, profile=creator)).public_allowed:
        raise MarketplaceError("Creator is not eligible for marketplace authoring")
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
        asset_result = await db.scalars(
            select(MediaAsset)
            .join(
                MediaDerivative,
                MediaDerivative.media_asset_id == MediaAsset.id,
            )
            .where(
                MediaAsset.id.in_(media_asset_ids),
                MediaAsset.owner_creator_id == creator_id,
                MediaAsset.status == MediaStatus.ready,
                MediaAsset.deleted_at.is_(None),
                MediaAsset.moderation_status == ModerationStatus.approved,
                MediaAsset.audience == MediaAudience.safe_public,
                MediaDerivative.derivative_type == DerivativeType.display,
                MediaDerivative.status == MediaStatus.ready,
                *listing_media_exclusivity_predicates(MediaAsset.id),
            )
        )
        assets = asset_result.unique().all()
        if len(assets) != len(media_asset_ids):
            raise MarketplaceError(
                "Listing media must be approved, safe-public, creator-owned, and dedicated to marketplace display"
            )
        for asset in assets:
            await require_media_context_available(db, asset.id, context_type="marketplace")
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
    creator = await db.get(CreatorProfile, listing.owner_creator_id)
    if (
        not creator
        or not (await resolve_creator_compliance_eligibility(db, profile=creator)).public_allowed
    ):
        raise MarketplaceError("Creator is not eligible for marketplace authoring")
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
    if approved:
        creator = await db.get(CreatorProfile, listing.owner_creator_id)
        if (
            not creator
            or not (
                await resolve_creator_compliance_eligibility(db, profile=creator)
            ).public_allowed
        ):
            raise MarketplaceError("Creator is not eligible for marketplace publication")
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
    compliance_decisions: dict[ComplianceFeature, ComplianceDecision] | None = None,
) -> MarketplaceOrder:
    """Reserve stock and snapshot server-owned pricing and shipping treatment."""
    if not idempotency_key or len(idempotency_key) > 128:
        raise MarketplaceError("A valid Idempotency-Key is required")
    if quantity <= 0:
        raise MarketplaceError("Quantity must be positive")
    existing_attempt = await lock_payment_idempotency(db, buyer.id, idempotency_key)
    existing = await db.scalar(
        select(MarketplaceOrder)
        .join(PaymentAttempt)
        .where(
            PaymentAttempt.buyer_user_id == buyer.id,
            PaymentAttempt.idempotency_key == idempotency_key,
        )
    )
    if existing:
        if existing.status is MarketplaceOrderStatus.cancelled:
            raise MarketplaceTerminalPaymentError(existing)
        return existing
    if existing_attempt is not None:
        raise MarketplaceError("Idempotency-Key is already used by another payment command")
    listing = await db.scalar(
        select(MarketplaceListing).where(MarketplaceListing.id == listing_id).with_for_update()
    )
    if not listing or listing.status is not MarketplaceListingStatus.published:
        raise MarketplaceError("Marketplace listing is not available")
    try:
        seller = await require_public_creator_access(db, listing.owner_creator_id, buyer.id)
    except ValueError as exc:
        raise MarketplaceError("Marketplace listing is not available") from exc
    if seller.user_id == buyer.id:
        raise MarketplaceError("Creators cannot purchase their own listing")
    await require_marketplace_purchase_compliance(db, buyer, compliance_decisions)
    if (await seller_risk_profile(db, listing.owner_creator_id)).marketplace_suspended:
        raise MarketplaceError("Marketplace listing is not available")
    if listing.moderation_status is not ModerationStatus.approved:
        raise MarketplaceError("Marketplace listing is not approved")
    if listing.quantity_available < quantity:
        raise MarketplaceError("Marketplace listing is sold out")
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
        provider_reference=new_provider_reference(),
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
    order, attempt = await _lock_order_payment_attempt(db, order_id)
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
    if attempt.status is PaymentStatus.pending:
        attempt.status = PaymentStatus.failed
    await record_event(
        db,
        "marketplace.order_reservation_released",
        actor_user_id=order.buyer_user_id,
        target_type="marketplace_order",
        target_id=str(order.id),
        metadata={"reason": reason},
    )
    await emit_transactional(
        db,
        recipient_user_id=order.buyer_user_id,
        notification_type="MARKETPLACE_ORDER_CANCELLED",
        source_domain="marketplace",
        source_id=str(order.id),
        title="Order payment was not completed",
        body="Your order reservation has been cancelled.",
        target_path="/marketplace/orders",
    )
    return order


async def expire_marketplace_reservations(db: AsyncSession, limit: int = 100) -> int:
    """Durably cancel expired unconfirmed orders and restore stock under row locks."""
    order_ids = (
        await db.scalars(
            select(MarketplaceOrder.id)
            .where(
                MarketplaceOrder.status == MarketplaceOrderStatus.awaiting_payment,
                MarketplaceOrder.reservation_expires_at <= datetime.now(UTC),
            )
            .order_by(MarketplaceOrder.reservation_expires_at)
            .limit(limit)
        )
    ).all()
    released = 0
    for order_id in order_ids:
        if (
            await release_order_reservation(db, order_id, "payment_reservation_expired")
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
    from app.referrals.service import record_revenue_allocation, revenue_allocation

    referral_entries, referral_allocation = await revenue_allocation(
        db,
        buyer_user_id=order.buyer_user_id,
        revenue_type="marketplace",
        currency=order.currency,
        platform_fee_minor=fee,
        occurred_at=event_at,
    )
    referral_amount = int(referral_allocation["amount_minor"]) if referral_allocation else 0
    order.platform_fee_minor = fee
    order.creator_amount_minor = int(allocation_metadata["creator_amount_minor"])
    order.group_amount_minor = int(allocation_metadata["group_amount_minor"])
    clearing = await _account(db, LedgerAccountKind.platform_clearing, order.currency)
    revenue = await _account(db, LedgerAccountKind.platform_revenue, order.currency)
    entries = [
        (clearing, LedgerDirection.debit, order.total_paid_minor),
        (revenue, LedgerDirection.credit, order.platform_fee_minor - referral_amount),
        *referral_entries,
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
            "referral_amount_minor": str(referral_amount),
            "total_paid_minor": str(order.total_paid_minor),
            **allocation_metadata,
        },
    )
    await record_revenue_allocation(
        db,
        source_ledger_transaction_id=ledger.id,
        allocation=referral_allocation,
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
    seller = await db.get(CreatorProfile, order.seller_creator_id)
    if seller:
        await emit_transactional(
            db,
            recipient_user_id=order.buyer_user_id,
            notification_type="MARKETPLACE_ORDER_PLACED",
            source_domain="marketplace",
            source_id=str(order.id),
            title="Order confirmed",
            body=f"Your paid order total is {order.total_paid_minor} {order.currency}.",
            target_path="/marketplace/orders",
        )
        await emit_transactional(
            db,
            recipient_user_id=seller.user_id,
            notification_type="MARKETPLACE_ORDER_PLACED",
            source_domain="marketplace",
            source_id=f"seller:{order.id}",
            title="You have a new paid order",
            body="Open FanBackstage to fulfil the order.",
            target_path="/marketplace/orders",
        )
    return order


async def settle_or_contain_payment_attempt(
    db: AsyncSession, attempt: PaymentAttempt
) -> MarketplaceOrder | None:
    """Settle a live reservation or freeze a capture after stock was restored."""
    order = await db.scalar(
        select(MarketplaceOrder)
        .where(MarketplaceOrder.payment_attempt_id == attempt.id)
        .with_for_update()
    )
    if order is None:
        return None
    if attempt.status is not PaymentStatus.succeeded:
        return order
    if order.status is MarketplaceOrderStatus.awaiting_payment:
        return await settle_order(db, order)
    if order.status is MarketplaceOrderStatus.cancelled:
        from app.finance.service import record_excess_capture

        await record_excess_capture(
            db,
            attempt,
            source_type=ExcessCaptureSource.marketplace_order,
            source_reference=order.id,
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
    await emit_transactional(
        db,
        recipient_user_id=order.buyer_user_id,
        notification_type="MARKETPLACE_ORDER_SHIPPED",
        source_domain="marketplace",
        source_id=str(order.id),
        title="Your order has shipped",
        body="Open FanBackstage for the operational order status.",
        target_path="/marketplace/orders",
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
    await emit_transactional(
        db,
        recipient_user_id=order.buyer_user_id,
        notification_type="MARKETPLACE_ORDER_DELIVERED",
        source_domain="marketplace",
        source_id=str(order.id),
        title="Order delivered",
        body="Your delivery has been confirmed.",
        target_path="/marketplace/orders",
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
    from app.referrals.service import release_entries

    referral_release_entries, referral_allocation = await release_entries(db, original.id)
    entries.extend(referral_release_entries)
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
            "referral_amount_minor": str(referral_allocation.amount_minor)
            if referral_allocation
            else "0",
        },
    )
    if referral_allocation and not referral_allocation.released_at:
        referral_allocation.released_at = now
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


async def _marketplace_dispute_ledger_state(
    db: AsyncSession, order: MarketplaceOrder
) -> tuple[LedgerTransaction, LedgerTransaction | None, LedgerTransaction | None, int]:
    """Lock one order's settlement/release chain and return its active hold.

    Callers already hold the payment attempt and order, preserving the shared
    provider/admin lock order before ledger rows and their accounts are locked.
    A release, temporary hold, and seller-favour restoration remain separate
    immutable transactions linked through ``reversal_of_transaction_id``.
    """
    if not order.ledger_transaction_id:
        raise MarketplaceError("Order settlement ledger is missing")
    original = await db.scalar(
        select(LedgerTransaction)
        .where(LedgerTransaction.id == order.ledger_transaction_id)
        .with_for_update()
    )
    if original is None:
        raise MarketplaceError("Order settlement ledger is missing")
    release = await db.scalar(
        select(LedgerTransaction)
        .where(LedgerTransaction.idempotency_key == f"marketplace-release:{order.id}")
        .with_for_update()
    )
    holds = (
        await db.scalars(
            select(LedgerTransaction)
            .where(
                LedgerTransaction.transaction_type == LedgerTransactionType.payment_dispute_hold,
                LedgerTransaction.metadata_json["marketplace_order_id"].astext == str(order.id),
            )
            .order_by(LedgerTransaction.created_at, LedgerTransaction.id)
            .with_for_update()
        )
    ).all()
    restored_hold_ids: set[UUID] = set()
    if holds:
        restored_hold_ids = set(
            await db.scalars(
                select(LedgerTransaction.reversal_of_transaction_id)
                .where(
                    LedgerTransaction.transaction_type == LedgerTransactionType.earnings_release,
                    LedgerTransaction.reversal_of_transaction_id.in_([row.id for row in holds]),
                    LedgerTransaction.metadata_json["marketplace_dispute_operation"].astext
                    == "restore",
                )
                .with_for_update()
            )
        )
    active_hold = next(
        (row for row in reversed(holds) if row.id not in restored_hold_ids),
        None,
    )
    if order.earnings_released_at and release is None:
        raise MarketplaceError("Order earnings release ledger is missing")
    return original, release, active_hold, len(holds)


async def _inverse_locked_ledger_entries(
    db: AsyncSession, transaction: LedgerTransaction
) -> list[tuple[LedgerAccount, LedgerDirection, int]]:
    """Lock source accounts in stable order and invert one immutable movement."""
    source_entries = (
        await db.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.transaction_id == transaction.id)
            .order_by(LedgerEntry.ledger_account_id, LedgerEntry.id)
        )
    ).all()
    if not source_entries:
        raise MarketplaceError("Marketplace allocation ledger entries are missing")
    accounts = (
        await db.scalars(
            select(LedgerAccount)
            .where(
                LedgerAccount.id.in_(
                    sorted({entry.ledger_account_id for entry in source_entries}, key=str)
                )
            )
            .order_by(LedgerAccount.id)
            .with_for_update()
        )
    ).all()
    account_by_id = {account.id: account for account in accounts}
    if len(account_by_id) != len({entry.ledger_account_id for entry in source_entries}):
        raise MarketplaceError("Marketplace allocation ledger account is missing")
    return [
        (
            account_by_id[entry.ledger_account_id],
            LedgerDirection.credit
            if entry.direction is LedgerDirection.debit
            else LedgerDirection.debit,
            entry.amount_minor,
        )
        for entry in source_entries
    ]


def _marketplace_dispute_metadata(
    order: MarketplaceOrder,
    original: LedgerTransaction,
    release: LedgerTransaction,
) -> dict[str, str]:
    original_metadata = original.metadata_json or {}
    release_metadata = release.metadata_json or {}
    keys = {
        "creator_id",
        "creator_amount_minor",
        "creator_pool_minor",
        "group_id",
        "group_amount_minor",
        "group_contract_id",
        "group_contract_version",
        "creator_basis_points",
        "group_basis_points",
        "referral_amount_minor",
        "shipping_pass_through_minor",
    }
    frozen = {
        key: str(release_metadata.get(key, original_metadata.get(key, "")))
        for key in keys
        if key in original_metadata or key in release_metadata
    }
    return {
        **frozen,
        "marketplace_order_id": str(order.id),
        "payment_attempt_id": str(order.payment_attempt_id),
        "original_ledger_transaction_id": str(original.id),
        "earnings_release_ledger_transaction_id": str(release.id),
        "reverses_exact_frozen_allocation": "true",
    }


async def _hold_released_order_allocation(
    db: AsyncSession,
    order: MarketplaceOrder,
    *,
    actor_user_id: UUID | None,
    reason: str,
    provider_event_id: str | None,
) -> LedgerTransaction | None:
    """Move this order's exact released allocation back to pending once."""
    original, release, active_hold, hold_count = await _marketplace_dispute_ledger_state(db, order)
    if active_hold:
        return active_hold
    if release is None:
        return None
    entries = await _inverse_locked_ledger_entries(db, release)
    cycle = hold_count + 1
    return await post_entries(
        db,
        transaction_type=LedgerTransactionType.payment_dispute_hold,
        currency=order.currency,
        idempotency_key=f"marketplace-dispute-hold:{order.id}:{cycle}",
        reference=f"marketplace_dispute_hold:{order.id}:{cycle}",
        reversal_of_transaction_id=release.id,
        entries=entries,
        metadata={
            **_marketplace_dispute_metadata(order, original, release),
            "marketplace_dispute_operation": "hold",
            "dispute_cycle": str(cycle),
            "actor_user_id": str(actor_user_id) if actor_user_id else "",
            "provider_event_id": provider_event_id or "",
            "reason": reason,
        },
    )


async def _restore_held_order_allocation(
    db: AsyncSession, order: MarketplaceOrder, *, reason: str
) -> LedgerTransaction | None:
    """Restore one active released-allocation hold after seller-favour resolution."""
    original, release, active_hold, _hold_count = await _marketplace_dispute_ledger_state(db, order)
    if active_hold is None:
        return None
    assert release is not None
    entries = await _inverse_locked_ledger_entries(db, active_hold)
    cycle = str((active_hold.metadata_json or {}).get("dispute_cycle", "1"))
    return await post_entries(
        db,
        transaction_type=LedgerTransactionType.earnings_release,
        currency=order.currency,
        idempotency_key=f"marketplace-dispute-restore:{active_hold.id}",
        reference=f"marketplace_dispute_restore:{active_hold.id}",
        reversal_of_transaction_id=active_hold.id,
        entries=entries,
        metadata={
            **_marketplace_dispute_metadata(order, original, release),
            "marketplace_dispute_operation": "restore",
            "dispute_cycle": cycle,
            "restores_dispute_hold_transaction_id": str(active_hold.id),
            "reason": reason,
        },
    )


async def _reverse_order_allocation(
    db: AsyncSession,
    order: MarketplaceOrder,
    *,
    transaction_type: LedgerTransactionType,
    reason: str,
) -> None:
    original, _release, active_hold, _hold_count = await _marketplace_dispute_ledger_state(
        db, order
    )
    existing = await db.scalar(
        select(LedgerTransaction)
        .where(
            LedgerTransaction.reversal_of_transaction_id == original.id,
            LedgerTransaction.transaction_type.in_(
                [LedgerTransactionType.refund, LedgerTransactionType.chargeback]
            ),
        )
        .order_by(LedgerTransaction.created_at, LedgerTransaction.id)
        .with_for_update()
    )
    if existing:
        return
    from app.referrals.service import reversal_entries

    referral_reversal_entries, referral_allocation = await reversal_entries(
        db,
        original.id,
        released_allocation_held=active_hold is not None,
    )
    referral_amount = referral_allocation.amount_minor if referral_allocation else 0
    creator_amount = int(original.metadata_json["creator_amount_minor"]) + int(
        original.metadata_json.get("shipping_pass_through_minor", 0)
    )
    group_amount = int(original.metadata_json.get("group_amount_minor", 0))
    clearing = await _account(db, LedgerAccountKind.platform_clearing, order.currency)
    revenue = await _account(db, LedgerAccountKind.platform_revenue, order.currency)
    creator_account = await _account(
        db,
        (
            LedgerAccountKind.creator_available
            if order.earnings_released_at and active_hold is None
            else LedgerAccountKind.creator_pending
        ),
        order.currency,
        order.seller_creator_id,
    )
    entries = [
        (clearing, LedgerDirection.credit, order.total_paid_minor),
        (revenue, LedgerDirection.debit, order.platform_fee_minor - referral_amount),
        *referral_reversal_entries,
    ]
    entries.append((creator_account, LedgerDirection.debit, creator_amount))
    if group_amount:
        group_id = original.metadata_json.get("group_id")
        if not group_id:
            raise MarketplaceError("Marketplace group allocation snapshot is incomplete")
        group_account = await _account(
            db,
            (
                LedgerAccountKind.group_available
                if order.earnings_released_at and active_hold is None
                else LedgerAccountKind.group_pending
            ),
            order.currency,
            owner_group_id=UUID(group_id),
        )
        entries.append((group_account, LedgerDirection.debit, group_amount))
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
            "dispute_hold_ledger_transaction_id": str(active_hold.id) if active_hold else "",
            "original_group_contract_id": original.metadata_json.get("group_contract_id", ""),
            "creator_amount_minor": str(creator_amount),
            "group_amount_minor": str(group_amount),
            "referral_amount_minor": str(referral_amount),
        },
    )
    if referral_allocation and not referral_allocation.reversed_at:
        referral_allocation.reversed_at = datetime.now(UTC)


async def _lock_order_payment_attempt(
    db: AsyncSession, order_id: UUID
) -> tuple[MarketplaceOrder, PaymentAttempt]:
    """Use the provider callback's attempt-before-domain lock order."""
    payment_attempt_id = await db.scalar(
        select(MarketplaceOrder.payment_attempt_id).where(MarketplaceOrder.id == order_id)
    )
    if payment_attempt_id is None:
        raise MarketplaceError("Marketplace order not found")
    attempt = await db.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == payment_attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if attempt is None:
        raise MarketplaceError("Marketplace payment attempt is missing")
    order = await db.scalar(
        select(MarketplaceOrder)
        .where(MarketplaceOrder.id == order_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if order is None:
        raise MarketplaceError("Marketplace order not found")
    if order.payment_attempt_id != attempt.id:
        raise MarketplaceError("Marketplace payment attempt changed; retry the command")
    return order, attempt


async def refund_order(
    db: AsyncSession, order_id: UUID, actor: User | None, reason: str
) -> MarketplaceOrder:
    order, attempt = await _lock_order_payment_attempt(db, order_id)
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
    attempt.status = PaymentStatus.refunded
    await record_event(
        db,
        "marketplace.order_refunded",
        actor_user_id=actor.id if actor else None,
        target_type="marketplace_order",
        target_id=str(order.id),
        metadata={"reason": reason, "stock_restored": restore_stock},
    )
    await emit_transactional(
        db,
        recipient_user_id=order.buyer_user_id,
        notification_type="MARKETPLACE_ORDER_REFUNDED",
        source_domain="marketplace",
        source_id=str(order.id),
        title="Order refunded",
        body="Your marketplace order refund has been completed.",
        target_path="/marketplace/orders",
    )
    return order


async def cancel_order(
    db: AsyncSession, order_id: UUID, actor: User, creator_id: UUID, reason: str
) -> MarketplaceOrder:
    """Seller cancellation is permitted only before the immutable shipment event."""
    order, _attempt = await _lock_order_payment_attempt(db, order_id)
    if (
        not order
        or order.status
        not in {
            MarketplaceOrderStatus.paid,
            MarketplaceOrderStatus.processing,
        }
        or order.seller_creator_id != creator_id
    ):
        raise MarketplaceError("Only an unshipped paid order can be cancelled")
    return await refund_order(db, order.id, actor, reason)


async def open_order_dispute(
    db: AsyncSession, order_id: UUID, buyer: User, reason: str
) -> MarketplaceOrder:
    order, attempt = await _lock_order_payment_attempt(db, order_id)
    if order.buyer_user_id != buyer.id or order.status not in {
        MarketplaceOrderStatus.paid,
        MarketplaceOrderStatus.processing,
        MarketplaceOrderStatus.shipped,
        MarketplaceOrderStatus.delivered,
        MarketplaceOrderStatus.disputed,
    }:
        raise MarketplaceError("Marketplace order cannot be disputed")
    return await block_order_for_dispute(db, order, attempt, buyer.id, reason)


async def _prior_status_for_active_dispute(
    db: AsyncSession, order: MarketplaceOrder
) -> tuple[MarketplaceOrderStatus, AuditEvent]:
    """Resolve exact lifecycle provenance from the append-only dispute event."""
    dispute_events = (
        await db.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == "marketplace.order_disputed",
                AuditEvent.target_type == "marketplace_order",
                AuditEvent.target_id == str(order.id),
            )
        )
    ).all()
    resolution_events = (
        await db.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == "marketplace.dispute_resolved",
                AuditEvent.target_type == "marketplace_order",
                AuditEvent.target_id == str(order.id),
            )
        )
    ).all()
    resolved_dispute_event_ids: set[UUID] = set()
    for resolution_event in resolution_events:
        raw_identifier = (resolution_event.metadata_json or {}).get("dispute_audit_event_id")
        if raw_identifier is None:
            continue
        try:
            resolved_dispute_event_ids.add(UUID(raw_identifier))
        except (AttributeError, TypeError, ValueError) as exc:
            raise MarketplaceError("Marketplace dispute lifecycle provenance is invalid") from exc
    active_events = [
        event for event in dispute_events if event.id not in resolved_dispute_event_ids
    ]
    if len(active_events) != 1:
        raise MarketplaceError("Marketplace dispute lifecycle provenance is missing")
    event = active_events[0]
    raw_status = (event.metadata_json or {}).get("prior_order_status")
    try:
        prior_status = MarketplaceOrderStatus(raw_status)
    except (TypeError, ValueError) as exc:
        raise MarketplaceError("Marketplace dispute lifecycle provenance is missing") from exc
    if prior_status not in {
        MarketplaceOrderStatus.paid,
        MarketplaceOrderStatus.processing,
        MarketplaceOrderStatus.shipped,
        MarketplaceOrderStatus.delivered,
    }:
        raise MarketplaceError("Marketplace dispute lifecycle provenance is invalid")
    if order.paid_at is None:
        raise MarketplaceError("Marketplace dispute lifecycle provenance is invalid")
    if prior_status in {MarketplaceOrderStatus.paid, MarketplaceOrderStatus.processing} and (
        order.shipped_at is not None or order.delivered_at is not None
    ):
        raise MarketplaceError("Marketplace dispute lifecycle provenance is inconsistent")
    if prior_status is MarketplaceOrderStatus.shipped and (
        order.shipped_at is None or order.delivered_at is not None
    ):
        raise MarketplaceError("Marketplace dispute lifecycle provenance is inconsistent")
    if prior_status is MarketplaceOrderStatus.delivered and (
        order.shipped_at is None or order.delivered_at is None
    ):
        raise MarketplaceError("Marketplace dispute lifecycle provenance is inconsistent")
    return prior_status, event


async def block_order_for_dispute(
    db: AsyncSession,
    order: MarketplaceOrder,
    attempt: PaymentAttempt,
    actor_user_id: UUID | None,
    reason: str,
    *,
    provider_event_id: str | None = None,
) -> MarketplaceOrder:
    """Persist a provider or buyer dispute as an earnings-release blocker."""
    if order.payment_attempt_id != attempt.id:
        raise MarketplaceError("Marketplace payment attempt changed; retry the command")
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
    prior_order_status = order.status
    hold = await _hold_released_order_allocation(
        db,
        order,
        actor_user_id=actor_user_id,
        reason=reason,
        provider_event_id=provider_event_id,
    )
    order.status = MarketplaceOrderStatus.disputed
    order.earnings_release_status = MarketplaceEarningsReleaseStatus.blocked
    order.release_block_reason = "unresolved_dispute"
    await record_event(
        db,
        "marketplace.order_disputed",
        actor_user_id=actor_user_id,
        target_type="marketplace_order",
        target_id=str(order.id),
        metadata={
            "reason": reason,
            "prior_order_status": prior_order_status.value,
            "dispute_hold_ledger_transaction_id": str(hold.id) if hold else "",
        },
    )
    return order


async def resolve_order_dispute(
    db: AsyncSession, order_id: UUID, actor: User | None, refund: bool, reason: str
) -> MarketplaceOrder:
    if refund:
        order, _attempt = await _lock_order_payment_attempt(db, order_id)
        if order.status is not MarketplaceOrderStatus.disputed:
            raise MarketplaceError("Marketplace dispute is not open")
        return await refund_order(db, order.id, actor, reason)
    order, attempt = await _lock_order_payment_attempt(db, order_id)
    if order.status is not MarketplaceOrderStatus.disputed:
        _original, _release, active_hold, hold_count = await _marketplace_dispute_ledger_state(
            db, order
        )
        if (
            hold_count
            and active_hold is None
            and order.status
            in {
                MarketplaceOrderStatus.shipped,
                MarketplaceOrderStatus.delivered,
            }
        ):
            return order
        raise MarketplaceError("Marketplace dispute is not open")
    prior_order_status, dispute_event = await _prior_status_for_active_dispute(db, order)
    restored = await _restore_held_order_allocation(db, order, reason=reason)
    order.status = prior_order_status
    order.earnings_release_status = (
        MarketplaceEarningsReleaseStatus.released
        if order.earnings_released_at
        else MarketplaceEarningsReleaseStatus.pending
    )
    order.release_block_reason = None
    if attempt.status is PaymentStatus.disputed:
        attempt.status = PaymentStatus.succeeded
    await record_event(
        db,
        "marketplace.dispute_resolved",
        actor_user_id=actor.id if actor else None,
        target_type="marketplace_order",
        target_id=str(order.id),
        metadata={
            "resolution": "seller_favour",
            "reason": reason,
            "dispute_audit_event_id": str(dispute_event.id),
            "restored_order_status": prior_order_status.value,
            "restoration_ledger_transaction_id": str(restored.id) if restored else "",
        },
    )
    return order


async def chargeback_order(
    db: AsyncSession, order_id: UUID, actor: User | None, reason: str
) -> MarketplaceOrder:
    order, attempt = await _lock_order_payment_attempt(db, order_id)
    if order.status is MarketplaceOrderStatus.chargeback:
        return order
    await _reverse_order_allocation(
        db, order, transaction_type=LedgerTransactionType.chargeback, reason=reason
    )
    order.status = MarketplaceOrderStatus.chargeback
    order.earnings_release_status = MarketplaceEarningsReleaseStatus.blocked
    order.release_block_reason = "chargeback"
    attempt.status = PaymentStatus.chargeback
    await record_event(
        db,
        "marketplace.order_chargeback",
        actor_user_id=actor.id if actor else None,
        target_type="marketplace_order",
        target_id=str(order.id),
        metadata={"reason": reason},
    )
    await emit_transactional(
        db,
        recipient_user_id=order.buyer_user_id,
        notification_type="MARKETPLACE_ORDER_CHARGEBACK",
        source_domain="marketplace",
        source_id=str(order.id),
        title="Order payment reversed",
        body="Your marketplace order payment was reversed.",
        target_path="/marketplace/orders",
    )
    return order
