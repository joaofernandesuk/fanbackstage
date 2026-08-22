from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db
from app.groups.service import has_delegated_permission
from app.marketplace import service
from app.media.service import approved_creator
from app.models.groups import GroupPermission
from app.models.marketplace import MarketplaceListing, MarketplaceListingStatus, MarketplaceOrder
from app.permissions.policies import Permission, authorize
from app.schemas.marketplace import (
    MarketplaceCheckoutInput,
    MarketplaceListingCreate,
    MarketplaceListingResponse,
    MarketplaceOrderResponse,
    MarketplaceShippingAddressResponse,
)

router = APIRouter(prefix="/marketplace", tags=["marketplace"])


def listing_response(listing: MarketplaceListing) -> MarketplaceListingResponse:
    return MarketplaceListingResponse(
        id=listing.id,
        public_id=listing.public_id,
        owner_creator_id=listing.owner_creator_id,
        title=listing.title,
        description=listing.description,
        category=listing.category,
        condition=listing.condition.value,
        status=listing.status.value,
        quantity_available=listing.quantity_available,
        price_amount_minor=listing.price_amount_minor,
        currency=listing.currency,
        shipping_mode=listing.shipping_mode.value,
        origin_country_code=listing.origin_country_code,
        shipping_charged_minor=listing.shipping_charged_minor,
    )


def order_response(order: MarketplaceOrder) -> MarketplaceOrderResponse:
    return MarketplaceOrderResponse(
        id=order.id,
        public_id=order.public_id,
        listing_id=order.listing_id,
        status=order.status.value,
        quantity=order.quantity,
        currency=order.currency,
        item_subtotal_minor=order.item_subtotal_minor,
        shipping_charged_minor=order.shipping_charged_minor,
        shipping_allowance_minor=order.shipping_allowance_minor,
        shipping_pass_through_minor=order.shipping_pass_through_minor,
        shipping_excess_minor=order.shipping_excess_minor,
        commissionable_base_minor=order.commissionable_base_minor,
        platform_fee_minor=order.platform_fee_minor,
        creator_amount_minor=order.creator_amount_minor,
        group_amount_minor=order.group_amount_minor,
        total_paid_minor=order.total_paid_minor,
        payment_attempt_id=order.payment_attempt_id,
    )


async def _create(
    payload: MarketplaceListingCreate, identity: CurrentIdentity, db: Db, creator_id: UUID
) -> MarketplaceListingResponse:
    try:
        listing = await service.create_listing(
            db,
            identity[0],
            creator_id=creator_id,
            **payload.model_dump(),
        )
        await db.commit()
        return listing_response(listing)
    except (PermissionError, service.MarketplaceError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)
        ) from exc


@router.post("/listings", response_model=MarketplaceListingResponse)
async def create_listing(
    payload: MarketplaceListingCreate, identity: CurrentIdentity, db: Db
) -> MarketplaceListingResponse:
    creator = await approved_creator(db, identity[0])
    return await _create(payload, identity, db, creator.id)


@router.post("/managed/{creator_id}/listings", response_model=MarketplaceListingResponse)
async def create_managed_listing(
    creator_id: UUID, payload: MarketplaceListingCreate, identity: CurrentIdentity, db: Db
) -> MarketplaceListingResponse:
    if not await has_delegated_permission(
        db, identity[0].id, creator_id, GroupPermission.manage_marketplace
    ):
        raise HTTPException(status_code=403, detail="Delegated marketplace permission denied")
    return await _create(payload, identity, db, creator_id)


@router.get("/listings/mine", response_model=list[MarketplaceListingResponse])
async def my_listings(identity: CurrentIdentity, db: Db) -> list[MarketplaceListingResponse]:
    creator = await approved_creator(db, identity[0])
    rows = (
        await db.scalars(
            select(MarketplaceListing)
            .where(MarketplaceListing.owner_creator_id == creator.id)
            .order_by(MarketplaceListing.created_at.desc())
        )
    ).all()
    return [listing_response(row) for row in rows]


@router.post("/listings/{listing_id}/submit", response_model=MarketplaceListingResponse)
async def submit_listing(
    listing_id: UUID, identity: CurrentIdentity, db: Db
) -> MarketplaceListingResponse:
    creator = await approved_creator(db, identity[0])
    try:
        listing = await service.submit_listing_for_review(db, identity[0], listing_id, creator.id)
        await db.commit()
        return listing_response(listing)
    except service.MarketplaceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/listings", response_model=list[MarketplaceListingResponse])
async def public_listings(
    db: Db, creator_id: UUID | None = None
) -> list[MarketplaceListingResponse]:
    query = select(MarketplaceListing).where(
        MarketplaceListing.status == MarketplaceListingStatus.published
    )
    if creator_id:
        query = query.where(MarketplaceListing.owner_creator_id == creator_id)
    rows = (await db.scalars(query.order_by(MarketplaceListing.published_at.desc()))).all()
    return [listing_response(row) for row in rows]


@router.get("/listings/{public_id}", response_model=MarketplaceListingResponse)
async def public_listing(public_id: str, db: Db) -> MarketplaceListingResponse:
    listing = await db.scalar(
        select(MarketplaceListing).where(
            MarketplaceListing.public_id == public_id,
            MarketplaceListing.status == MarketplaceListingStatus.published,
        )
    )
    if not listing:
        raise HTTPException(status_code=404, detail="Marketplace listing not found")
    return listing_response(listing)


@router.post("/listings/{public_id}/checkout", response_model=MarketplaceOrderResponse)
async def checkout(
    public_id: str,
    payload: MarketplaceCheckoutInput,
    identity: CurrentIdentity,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> MarketplaceOrderResponse:
    listing = await db.scalar(
        select(MarketplaceListing).where(MarketplaceListing.public_id == public_id)
    )
    if not listing:
        raise HTTPException(status_code=404, detail="Marketplace listing not found")
    try:
        order = await service.initiate_order(
            db,
            identity[0],
            listing.id,
            payload.quantity,
            payload.destination_country_code,
            idempotency_key or "",
            payload.destination_region_code,
            payload.shipping_address.model_dump(),
        )
        await db.commit()
        return order_response(order)
    except service.MarketplaceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get(
    "/orders/{order_id}/shipping-address", response_model=MarketplaceShippingAddressResponse
)
async def order_shipping_address(
    order_id: UUID, identity: CurrentIdentity, db: Db
) -> MarketplaceShippingAddressResponse:
    try:
        address = await service.shipping_address_for_order(db, order_id, identity[0])
        await db.commit()
        return MarketplaceShippingAddressResponse(
            order_id=address.order_id,
            recipient_name=address.recipient_name,
            line1=address.line1,
            line2=address.line2,
            city=address.city,
            region_code=address.region_code,
            postal_code=address.postal_code,
            country_code=address.country_code,
        )
    except (PermissionError, service.MarketplaceError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 404, detail=str(exc)
        ) from exc


@router.post("/admin/listings/{listing_id}/moderation", response_model=MarketplaceListingResponse)
async def moderate_listing(
    listing_id: UUID, approved: bool, identity: CurrentIdentity, db: Db
) -> MarketplaceListingResponse:
    authorize(identity[0], Permission.MODERATION_ACCESS)
    try:
        listing = await service.decide_listing_moderation(db, identity[0], listing_id, approved)
        await db.commit()
        return listing_response(listing)
    except service.MarketplaceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
