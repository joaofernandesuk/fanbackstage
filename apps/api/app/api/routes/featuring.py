from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db
from app.audit.service import record_event
from app.featuring import service
from app.groups.service import has_delegated_permission
from app.models.creator import CreatorProfile
from app.models.featuring import FeatureBooking, FeaturePrice, FeatureSlot, FeatureSurface
from app.models.groups import GroupPermission
from app.models.identity import User
from app.permissions.policies import Permission, authorize
from app.schemas.featuring import BookingInput, PriceInput, SlotInput, SurfaceInput

router = APIRouter(prefix="/featuring", tags=["featuring"])


def booking_response(row: FeatureBooking) -> dict:
    return {
        "id": str(row.id),
        "public_id": row.public_id,
        "purchaser_user_id": str(row.purchaser_user_id),
        "actor_user_id": str(row.actor_user_id),
        "owner_creator_id": str(row.owner_creator_id),
        "surface_id": str(row.surface_id),
        "slot_id": str(row.slot_id),
        "target_type": row.target_type.value,
        "target_id": str(row.target_id),
        "status": row.status.value,
        "starts_at": row.starts_at,
        "ends_at": row.ends_at,
        "price_minor": row.price_minor,
        "currency": row.currency,
        "price_version": row.price_version,
        "payment_attempt_id": str(row.payment_attempt_id) if row.payment_attempt_id else None,
    }


@router.get("/inventory")
async def inventory(db: Db) -> list[dict]:
    surfaces = (await db.scalars(select(FeatureSurface).order_by(FeatureSurface.kind))).all()
    result: list[dict] = []
    for surface in surfaces:
        slots = (
            await db.scalars(
                select(FeatureSlot)
                .where(FeatureSlot.surface_id == surface.id)
                .order_by(FeatureSlot.position)
            )
        ).all()
        slot_rows = []
        for slot in slots:
            prices = (
                await db.scalars(
                    select(FeaturePrice)
                    .where(FeaturePrice.slot_id == slot.id, FeaturePrice.active.is_(True))
                    .order_by(
                        FeaturePrice.target_type,
                        FeaturePrice.duration_seconds,
                        FeaturePrice.version.desc(),
                    )
                )
            ).all()
            slot_rows.append(
                {
                    "id": str(slot.id),
                    "slot_key": slot.slot_key,
                    "position": slot.position,
                    "capacity": slot.capacity,
                    "active": slot.active,
                    "prices": [
                        {
                            "id": str(price.id),
                            "target_type": price.target_type.value,
                            "duration_seconds": price.duration_seconds,
                            "amount_minor": price.amount_minor,
                            "currency": price.currency,
                            "version": price.version,
                        }
                        for price in prices
                    ],
                }
            )
        result.append(
            {
                "id": str(surface.id),
                "kind": surface.kind.value,
                "status": surface.status.value,
                "cancellation_cutoff_seconds": surface.cancellation_cutoff_seconds,
                "slots": slot_rows,
            }
        )
    return result


@router.get("/bookings/mine")
async def my_bookings(identity: CurrentIdentity, db: Db) -> list[dict]:
    owned_creator_ids = select(CreatorProfile.id).where(CreatorProfile.user_id == identity[0].id)
    rows = (
        await db.scalars(
            select(FeatureBooking)
            .where(
                (FeatureBooking.purchaser_user_id == identity[0].id)
                | (FeatureBooking.actor_user_id == identity[0].id)
                | FeatureBooking.owner_creator_id.in_(owned_creator_ids)
            )
            .order_by(FeatureBooking.created_at.desc())
        )
    ).all()
    return [booking_response(row) for row in rows]


@router.get("/eligible-targets")
async def eligible_targets(identity: CurrentIdentity, db: Db) -> list[dict]:
    profiles = (await db.scalars(select(CreatorProfile))).all()
    result = []
    for profile in profiles:
        if profile.user_id == identity[0].id or await has_delegated_permission(
            db, identity[0].id, profile.id, GroupPermission.manage_featuring
        ):
            result.append(
                {
                    "target_type": "creator",
                    "target_id": str(profile.id),
                    "title": profile.display_name or profile.username or "Creator",
                    "owner_user_id": str(profile.user_id),
                }
            )
    return result


@router.post("/bookings")
async def create_booking(
    payload: BookingInput,
    identity: CurrentIdentity,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    try:
        owner_id = await service.assert_target_eligibility(
            db, service.FeatureTargetType(payload.target_type), payload.target_id
        )
        owner = await db.get(CreatorProfile, owner_id)
        assert owner
        payer = (
            identity[0]
            if payload.payer_user_id is None
            else await db.get(User, payload.payer_user_id)
        )
        if not payer:
            raise service.FeaturingError("Selected payer not found")
        if identity[0].id == owner.user_id and payer.id != identity[0].id:
            raise service.FeaturingError("Creators must authorize their own featuring payment")
        if identity[0].id != owner.user_id and payer.id not in {identity[0].id, owner.user_id}:
            raise service.FeaturingError("Manager payer must be the manager or target creator")
        row = await service.create_booking(
            db,
            actor=identity[0],
            purchaser=payer,
            slot_id=payload.slot_id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            starts_at=payload.starts_at,
            duration_seconds=payload.duration_seconds,
            idempotency_key=idempotency_key or "",
        )
        await db.commit()
        return booking_response(row)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.post("/bookings/{booking_id}/payment")
async def start_payment(booking_id: UUID, identity: CurrentIdentity, db: Db) -> dict:
    row = await db.get(FeatureBooking, booking_id)
    if not row:
        raise HTTPException(404, "Booking not found")
    try:
        attempt = await service.initiate_payment(db, row, identity[0])
        await db.commit()
        return {"payment_attempt_id": str(attempt.id), "status": attempt.status.value}
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.post("/bookings/{booking_id}/cancel")
async def cancel_booking(booking_id: UUID, identity: CurrentIdentity, db: Db) -> dict:
    row = await db.get(FeatureBooking, booking_id)
    if not row:
        raise HTTPException(404, "Booking not found")
    try:
        row = await service.cancel_before_start(db, row, identity[0])
        await db.commit()
        return booking_response(row)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.post("/admin/surfaces")
async def admin_surface(payload: SurfaceInput, identity: CurrentIdentity, db: Db) -> dict:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    row = await service.create_surface(
        db, identity[0], payload.kind, payload.cancellation_cutoff_seconds
    )
    await db.commit()
    return {"id": str(row.id), "kind": row.kind.value}


@router.post("/admin/slots")
async def admin_slot(payload: SlotInput, identity: CurrentIdentity, db: Db) -> dict:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    row = await service.create_slot(
        db, identity[0], payload.surface_id, payload.slot_key, payload.position, payload.capacity
    )
    await db.commit()
    return {"id": str(row.id)}


@router.post("/admin/prices")
async def admin_price(payload: PriceInput, identity: CurrentIdentity, db: Db) -> dict:
    authorize(identity[0], Permission.FINANCIAL_CONFIGURE)
    row = await service.create_price(
        db,
        identity[0],
        payload.slot_id,
        payload.target_type,
        payload.duration_seconds,
        payload.amount_minor,
        payload.currency,
    )
    await db.commit()
    return {"id": str(row.id), "version": row.version}


@router.get("/admin/bookings")
async def admin_bookings(identity: CurrentIdentity, db: Db) -> list[dict]:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    rows = (
        await db.scalars(select(FeatureBooking).order_by(FeatureBooking.created_at.desc()))
    ).all()
    return [booking_response(row) for row in rows]


@router.post("/admin/reconcile")
async def reconcile(identity: CurrentIdentity, db: Db) -> dict:
    """Operational replay-safe lifecycle reconciliation; normal execution is Celery-driven."""
    authorize(identity[0], Permission.FINANCIAL_CONFIGURE)
    expired = await service.expire_reservations(db)
    activated = await service.activate_due_bookings(db)
    revalidated = await service.revalidate_active_bookings(db)
    deactivated = await service.deactivate_due_bookings(db)
    await record_event(
        db,
        "featuring.lifecycle_reconciled",
        actor_user_id=identity[0].id,
        target_type="featuring_lifecycle",
        target_id="scheduled_bookings",
        metadata={
            "expired": expired,
            "activated": activated,
            "revalidated": revalidated,
            "deactivated": deactivated,
        },
    )
    await db.commit()
    return {
        "expired": expired,
        "activated": activated,
        "revalidated": revalidated,
        "deactivated": deactivated,
    }
