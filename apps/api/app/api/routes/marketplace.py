from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db, OptionalIdentity
from app.compliance.http import resolve_request_compliance_decision
from app.compliance.types import JurisdictionSignals
from app.creators.service import require_public_creator_access
from app.groups.service import has_delegated_permission
from app.marketplace import service
from app.media.projection import safe_public_profile_media_reference
from app.media.service import approved_creator
from app.models.compliance import ComplianceFeature
from app.models.content import ModerationStatus
from app.models.creator import CreatorProfile, CreatorStatus
from app.models.groups import GroupPermission
from app.models.marketplace import (
    MarketplaceListing,
    MarketplaceListingStatus,
    MarketplaceOrder,
    MarketplaceTrackingEvent,
)
from app.models.social import SocialReport
from app.permissions.policies import Permission, authorize
from app.schemas.marketplace import (
    MarketplaceCheckoutInput,
    MarketplaceDisputeResolutionInput,
    MarketplaceListingCreate,
    MarketplaceListingMediaResponse,
    MarketplaceListingResponse,
    MarketplaceOrderReasonInput,
    MarketplaceOrderResponse,
    MarketplaceSellerResponse,
    MarketplaceShipmentInput,
    MarketplaceShippingAddressResponse,
    MarketplaceTrackingEventResponse,
)
from app.schemas.social import ReportInput

router = APIRouter(prefix="/marketplace", tags=["marketplace"])


async def require_marketplace_authoring(db: Db, request: Request, user) -> None:
    for feature in (ComplianceFeature.platform_access, ComplianceFeature.marketplace):
        decision = await resolve_request_compliance_decision(
            db,
            request,
            user=user,
            feature=feature,
            adult_restricted=False,
        )
        if not decision.allowed:
            raise HTTPException(
                403,
                {
                    "message": decision.reason,
                    "code": decision.code,
                    "action": decision.action,
                    "reason": decision.reason,
                },
            )


def marketplace_error_detail(exc: service.MarketplaceError) -> str | dict[str, object]:
    if not exc.compliance_decision:
        return str(exc)
    return {
        "message": str(exc),
        "code": exc.code,
        "action": exc.action,
        "reason": exc.compliance_decision.reason,
    }


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


async def public_listing_response(
    db: Db, listing: MarketplaceListing
) -> MarketplaceListingResponse:
    result = listing_response(listing)
    creator = await db.get(CreatorProfile, listing.owner_creator_id)
    if creator and creator.username:
        result.seller = MarketplaceSellerResponse(
            creator_id=creator.id,
            username=creator.username,
            display_name=creator.display_name or creator.username,
            avatar_reference=safe_public_profile_media_reference(creator.avatar_reference),
            verified=True,
        )
    result.media = [
        MarketplaceListingMediaResponse(
            derivative_id=derivative.id,
            delivery_path=f"/media/previews/{derivative.id}",
            position=link.position,
            width=derivative.width,
            height=derivative.height,
        )
        for link, derivative in await service.public_listing_media(db, listing)
    ]
    return result


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
        carrier=order.carrier,
        tracking_reference=order.tracking_reference,
        shipped_at=order.shipped_at,
        delivered_at=order.delivered_at,
        earnings_hold_until=order.earnings_hold_until,
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
    payload: MarketplaceListingCreate,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
) -> MarketplaceListingResponse:
    await require_marketplace_authoring(db, request, identity[0])
    creator = await approved_creator(db, identity[0])
    return await _create(payload, identity, db, creator.id)


@router.post("/managed/{creator_id}/listings", response_model=MarketplaceListingResponse)
async def create_managed_listing(
    creator_id: UUID,
    payload: MarketplaceListingCreate,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
) -> MarketplaceListingResponse:
    await require_marketplace_authoring(db, request, identity[0])
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
    listing_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> MarketplaceListingResponse:
    await require_marketplace_authoring(db, request, identity[0])
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
    request: Request,
    db: Db,
    identity: OptionalIdentity,
    creator_id: UUID | None = None,
) -> list[MarketplaceListingResponse]:
    decision = await resolve_request_compliance_decision(
        db,
        request,
        user=identity[0] if identity else None,
        feature=ComplianceFeature.marketplace,
        adult_restricted=False,
    )
    if not decision.allowed:
        raise HTTPException(
            403,
            {
                "message": decision.reason,
                "code": decision.code,
                "action": decision.action,
                "reason": decision.reason,
            },
        )
    query = (
        select(MarketplaceListing)
        .join(CreatorProfile, CreatorProfile.id == MarketplaceListing.owner_creator_id)
        .where(
            MarketplaceListing.status == MarketplaceListingStatus.published,
            MarketplaceListing.moderation_status == ModerationStatus.approved,
            CreatorProfile.status == CreatorStatus.approved,
            CreatorProfile.is_public.is_(True),
        )
    )
    if creator_id:
        query = query.where(MarketplaceListing.owner_creator_id == creator_id)
    rows = (await db.scalars(query.order_by(MarketplaceListing.published_at.desc()))).all()
    viewer_id = identity[0].id if identity else None
    visible: list[MarketplaceListingResponse] = []
    for row in rows:
        try:
            await require_public_creator_access(db, row.owner_creator_id, viewer_id)
        except ValueError:
            continue
        visible.append(await public_listing_response(db, row))
    return visible


@router.get("/listings/{public_id}", response_model=MarketplaceListingResponse)
async def public_listing(
    public_id: str, request: Request, db: Db, identity: OptionalIdentity
) -> MarketplaceListingResponse:
    decision = await resolve_request_compliance_decision(
        db,
        request,
        user=identity[0] if identity else None,
        feature=ComplianceFeature.marketplace,
        adult_restricted=False,
    )
    if not decision.allowed:
        raise HTTPException(
            403,
            {
                "message": decision.reason,
                "code": decision.code,
                "action": decision.action,
                "reason": decision.reason,
            },
        )
    listing = await db.scalar(
        select(MarketplaceListing)
        .join(CreatorProfile, CreatorProfile.id == MarketplaceListing.owner_creator_id)
        .where(
            MarketplaceListing.public_id == public_id,
            MarketplaceListing.status == MarketplaceListingStatus.published,
            MarketplaceListing.moderation_status == ModerationStatus.approved,
            CreatorProfile.status == CreatorStatus.approved,
            CreatorProfile.is_public.is_(True),
        )
    )
    if not listing:
        raise HTTPException(status_code=404, detail="Marketplace listing not found")
    try:
        await require_public_creator_access(
            db, listing.owner_creator_id, identity[0].id if identity else None
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Marketplace listing not found") from exc
    return await public_listing_response(db, listing)


@router.post("/listings/{public_id}/report", response_model=dict)
async def report_listing(
    public_id: str, payload: ReportInput, identity: CurrentIdentity, db: Db
) -> dict:
    listing = await db.scalar(
        select(MarketplaceListing).where(
            MarketplaceListing.public_id == public_id,
            MarketplaceListing.status == MarketplaceListingStatus.published,
        )
    )
    if not listing:
        raise HTTPException(status_code=404, detail="Marketplace listing not found")
    existing = await db.scalar(
        select(SocialReport).where(
            SocialReport.reporter_user_id == identity[0].id,
            SocialReport.target_type == "marketplace_listing",
            SocialReport.target_id == listing.id,
            SocialReport.reason == payload.reason,
        )
    )
    if not existing:
        db.add(
            SocialReport(
                reporter_user_id=identity[0].id,
                target_type="marketplace_listing",
                target_id=listing.id,
                reason=payload.reason,
                details=payload.details,
            )
        )
    await db.commit()
    return {"reported": True}


@router.get("/orders/mine", response_model=list[MarketplaceOrderResponse])
async def buyer_orders(identity: CurrentIdentity, db: Db) -> list[MarketplaceOrderResponse]:
    rows = (
        await db.scalars(
            select(MarketplaceOrder)
            .where(MarketplaceOrder.buyer_user_id == identity[0].id)
            .order_by(MarketplaceOrder.created_at.desc())
        )
    ).all()
    return [order_response(row) for row in rows]


@router.get("/orders/fulfilment", response_model=list[MarketplaceOrderResponse])
async def creator_fulfilment_orders(
    identity: CurrentIdentity, db: Db
) -> list[MarketplaceOrderResponse]:
    creator = await approved_creator(db, identity[0])
    rows = (
        await db.scalars(
            select(MarketplaceOrder)
            .where(MarketplaceOrder.seller_creator_id == creator.id)
            .order_by(MarketplaceOrder.created_at.desc())
        )
    ).all()
    return [order_response(row) for row in rows]


async def require_managed_marketplace_orders(db: Db, actor_id: UUID, creator_id: UUID) -> None:
    if not await has_delegated_permission(
        db, actor_id, creator_id, GroupPermission.manage_marketplace_orders
    ):
        raise HTTPException(status_code=403, detail="Delegated marketplace order permission denied")


@router.get(
    "/managed/{creator_id}/orders/fulfilment", response_model=list[MarketplaceOrderResponse]
)
async def managed_creator_fulfilment_orders(
    creator_id: UUID, identity: CurrentIdentity, db: Db
) -> list[MarketplaceOrderResponse]:
    await require_managed_marketplace_orders(db, identity[0].id, creator_id)
    rows = (
        await db.scalars(
            select(MarketplaceOrder)
            .where(MarketplaceOrder.seller_creator_id == creator_id)
            .order_by(MarketplaceOrder.created_at.desc())
        )
    ).all()
    return [order_response(row) for row in rows]


@router.post("/orders/{order_id}/processing", response_model=MarketplaceOrderResponse)
async def order_processing(
    order_id: UUID, identity: CurrentIdentity, db: Db
) -> MarketplaceOrderResponse:
    creator = await approved_creator(db, identity[0])
    try:
        order = await service.mark_order_processing(db, order_id, identity[0], creator.id)
        await db.commit()
        return order_response(order)
    except service.MarketplaceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/managed/{creator_id}/orders/{order_id}/processing", response_model=MarketplaceOrderResponse
)
async def managed_order_processing(
    creator_id: UUID, order_id: UUID, identity: CurrentIdentity, db: Db
) -> MarketplaceOrderResponse:
    await require_managed_marketplace_orders(db, identity[0].id, creator_id)
    try:
        order = await service.mark_order_processing(db, order_id, identity[0], creator_id)
        await db.commit()
        return order_response(order)
    except service.MarketplaceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/orders/{order_id}/shipped", response_model=MarketplaceOrderResponse)
async def order_shipped(
    order_id: UUID, payload: MarketplaceShipmentInput, identity: CurrentIdentity, db: Db
) -> MarketplaceOrderResponse:
    creator = await approved_creator(db, identity[0])
    try:
        order = await service.mark_order_shipped(
            db, order_id, identity[0], creator.id, payload.carrier, payload.tracking_reference
        )
        await db.commit()
        return order_response(order)
    except service.MarketplaceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/managed/{creator_id}/orders/{order_id}/shipped", response_model=MarketplaceOrderResponse
)
async def managed_order_shipped(
    creator_id: UUID,
    order_id: UUID,
    payload: MarketplaceShipmentInput,
    identity: CurrentIdentity,
    db: Db,
) -> MarketplaceOrderResponse:
    await require_managed_marketplace_orders(db, identity[0].id, creator_id)
    try:
        order = await service.mark_order_shipped(
            db, order_id, identity[0], creator_id, payload.carrier, payload.tracking_reference
        )
        await db.commit()
        return order_response(order)
    except service.MarketplaceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/orders/{order_id}/delivered", response_model=MarketplaceOrderResponse)
async def order_delivered(
    order_id: UUID, identity: CurrentIdentity, db: Db
) -> MarketplaceOrderResponse:
    try:
        order = await service.confirm_order_delivery(db, order_id, identity[0])
        await db.commit()
        return order_response(order)
    except service.MarketplaceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/orders/{order_id}/cancel", response_model=MarketplaceOrderResponse)
async def cancel_order(
    order_id: UUID,
    payload: MarketplaceOrderReasonInput,
    identity: CurrentIdentity,
    db: Db,
) -> MarketplaceOrderResponse:
    creator = await approved_creator(db, identity[0])
    try:
        order = await service.cancel_order(db, order_id, identity[0], creator.id, payload.reason)
        await db.commit()
        return order_response(order)
    except service.MarketplaceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/orders/{order_id}/disputes", response_model=MarketplaceOrderResponse)
async def dispute_order(
    order_id: UUID,
    payload: MarketplaceOrderReasonInput,
    identity: CurrentIdentity,
    db: Db,
) -> MarketplaceOrderResponse:
    try:
        order = await service.open_order_dispute(db, order_id, identity[0], payload.reason)
        await db.commit()
        return order_response(order)
    except service.MarketplaceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/admin/orders/{order_id}/refund", response_model=MarketplaceOrderResponse)
async def admin_refund_order(
    order_id: UUID,
    payload: MarketplaceOrderReasonInput,
    identity: CurrentIdentity,
    db: Db,
) -> MarketplaceOrderResponse:
    authorize(identity[0], Permission.FINANCIAL_ACCESS)
    try:
        order = await service.refund_order(db, order_id, identity[0], payload.reason)
        await db.commit()
        return order_response(order)
    except service.MarketplaceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/admin/orders/{order_id}/chargeback", response_model=MarketplaceOrderResponse)
async def admin_chargeback_order(
    order_id: UUID,
    payload: MarketplaceOrderReasonInput,
    identity: CurrentIdentity,
    db: Db,
) -> MarketplaceOrderResponse:
    authorize(identity[0], Permission.FINANCIAL_ACCESS)
    try:
        order = await service.chargeback_order(db, order_id, identity[0], payload.reason)
        await db.commit()
        return order_response(order)
    except service.MarketplaceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/admin/orders/{order_id}/dispute-resolution", response_model=MarketplaceOrderResponse)
async def admin_resolve_order_dispute(
    order_id: UUID,
    payload: MarketplaceDisputeResolutionInput,
    identity: CurrentIdentity,
    db: Db,
) -> MarketplaceOrderResponse:
    authorize(identity[0], Permission.FINANCIAL_ACCESS)
    try:
        order = await service.resolve_order_dispute(
            db, order_id, identity[0], payload.refund, payload.reason
        )
        await db.commit()
        return order_response(order)
    except service.MarketplaceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/orders/{order_id}/tracking", response_model=list[MarketplaceTrackingEventResponse])
async def order_tracking(
    order_id: UUID, identity: CurrentIdentity, db: Db
) -> list[MarketplaceTrackingEventResponse]:
    order = await db.get(MarketplaceOrder, order_id)
    if not order or order.buyer_user_id != identity[0].id:
        creator = await db.scalar(
            select(CreatorProfile).where(CreatorProfile.user_id == identity[0].id)
        )
        if not order or creator.id != order.seller_creator_id:
            raise HTTPException(status_code=404, detail="Marketplace order not found")
    rows = (
        await db.scalars(
            select(MarketplaceTrackingEvent)
            .where(MarketplaceTrackingEvent.order_id == order_id)
            .order_by(MarketplaceTrackingEvent.created_at)
        )
    ).all()
    return [
        MarketplaceTrackingEventResponse(
            event_type=row.event_type,
            carrier=row.carrier,
            tracking_reference=row.tracking_reference,
            created_at=row.created_at,
        )
        for row in rows
    ]


@router.get("/orders/{order_id}", response_model=MarketplaceOrderResponse)
async def order_detail(
    order_id: UUID, identity: CurrentIdentity, db: Db
) -> MarketplaceOrderResponse:
    order = await db.scalar(
        select(MarketplaceOrder).where(
            MarketplaceOrder.id == order_id,
            MarketplaceOrder.buyer_user_id == identity[0].id,
        )
    )
    if not order:
        raise HTTPException(status_code=404, detail="Marketplace order not found")
    return order_response(order)


@router.post("/listings/{public_id}/checkout", response_model=MarketplaceOrderResponse)
async def checkout(
    public_id: str,
    payload: MarketplaceCheckoutInput,
    request: Request,
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
        decisions = {
            feature: await resolve_request_compliance_decision(
                db,
                request,
                user=identity[0],
                feature=feature,
                adult_restricted=False,
                signals=JurisdictionSignals(billing_country=payload.destination_country_code),
            )
            for feature in (ComplianceFeature.marketplace, ComplianceFeature.purchases)
        }
        order = await service.initiate_order(
            db,
            identity[0],
            listing.id,
            payload.quantity,
            payload.destination_country_code,
            idempotency_key or "",
            payload.destination_region_code,
            payload.shipping_address.model_dump(),
            decisions,
        )
        await db.commit()
        return order_response(order)
    except service.MarketplaceTerminalPaymentError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail={
                "code": "marketplace_payment_terminal",
                "message": str(exc),
                "order_id": str(exc.order_id),
                "status": exc.order_status.value,
            },
        ) from exc
    except service.MarketplaceError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if exc.compliance_decision else 400,
            detail=marketplace_error_detail(exc),
        ) from exc


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
