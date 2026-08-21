from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db
from app.audit.service import record_event
from app.finance import service
from app.media.service import approved_creator
from app.models.content import ContentItem
from app.models.creator import CreatorProfile
from app.models.finance import CommissionRule, PaymentAttempt, Purchase
from app.models.messaging import MessageUnlockPurchase, PendingMessageSend
from app.models.subscription import SubscriptionPeriod
from app.permissions.policies import Permission, authorize
from app.schemas.finance import (
    CommissionUpdate,
    CreatorEarningsResponse,
    PurchaseHistoryResponse,
    PurchaseResponse,
    RefundRequest,
)

router = APIRouter(tags=["finance"])


def purchase_response(purchase: Purchase) -> PurchaseResponse:
    return PurchaseResponse(
        id=purchase.id,
        content_id=purchase.content_id,
        status=purchase.status.value,
        gross_amount_minor=purchase.gross_amount_minor,
        platform_fee_minor=purchase.platform_fee_minor,
        creator_amount_minor=purchase.creator_amount_minor,
        currency=purchase.currency,
        payment_attempt_id=purchase.payment_attempt_id,
    )


@router.post("/purchases/content/{content_id}", response_model=PurchaseResponse)
async def start_purchase(
    content_id: UUID,
    identity: CurrentIdentity,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> PurchaseResponse:
    try:
        purchase = await service.initiate_purchase(
            db, identity[0], content_id, idempotency_key or ""
        )
        await db.commit()
        return purchase_response(purchase)
    except service.FinancialError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/purchases/mine", response_model=list[PurchaseHistoryResponse])
async def my_purchases(identity: CurrentIdentity, db: Db) -> list[PurchaseHistoryResponse]:
    rows = (
        await db.execute(
            select(Purchase, ContentItem.title, CreatorProfile.username)
            .join(ContentItem, ContentItem.id == Purchase.content_id)
            .join(CreatorProfile, CreatorProfile.id == Purchase.seller_creator_id)
            .where(Purchase.buyer_user_id == identity[0].id)
            .order_by(Purchase.created_at.desc())
        )
    ).all()
    return [
        PurchaseHistoryResponse(
            id=purchase.id,
            content_id=purchase.content_id,
            content_title=title,
            creator_username=username,
            gross_amount_minor=purchase.gross_amount_minor,
            currency=purchase.currency,
            status=purchase.status.value,
        )
        for purchase, title, username in rows
    ]


@router.post("/payments/development/{payment_attempt_id}/complete", response_model=PurchaseResponse)
async def complete_development_payment(
    payment_attempt_id: UUID, identity: CurrentIdentity, db: Db
) -> PurchaseResponse:
    attempt = await db.get(PaymentAttempt, payment_attempt_id)
    if not attempt or attempt.buyer_user_id != identity[0].id:
        raise HTTPException(status_code=404, detail="Payment attempt not found")
    if attempt.provider != "development":
        raise HTTPException(status_code=404, detail="Development payment is unavailable")
    payload, signature = service.development_webhook_payload(attempt)
    try:
        purchase = await service.process_development_webhook(db, payload, signature)
        await db.commit()
    except service.FinancialError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if purchase is None:
        purchase = await db.scalar(
            select(Purchase).where(Purchase.payment_attempt_id == attempt.id)
        )
    if not purchase:
        raise HTTPException(status_code=409, detail="Payment was already processed")
    return purchase_response(purchase)


@router.post("/payments/webhooks/development", status_code=204)
async def development_webhook(request: Request, db: Db) -> None:
    payload = await request.body()
    try:
        await service.process_development_webhook(
            db, payload, request.headers.get("X-Payment-Signature")
        )
        await db.commit()
    except service.FinancialError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/finance/creator/earnings", response_model=CreatorEarningsResponse)
async def creator_earnings(
    identity: CurrentIdentity, db: Db, currency: str = "EUR"
) -> CreatorEarningsResponse:
    creator = await approved_creator(db, identity[0])
    summary = await service.creator_financial_summary(db, creator.id, currency)
    return CreatorEarningsResponse(**summary, currency=currency.upper())


@router.post("/finance/creator/release", response_model=dict)
async def release_earnings(identity: CurrentIdentity, db: Db) -> dict:
    creator = await approved_creator(db, identity[0])
    ledger = await service.release_creator_earnings(db, creator.id, "EUR")
    if ledger:
        await record_event(
            db,
            "creator.earnings_released",
            actor_user_id=identity[0].id,
            target_type="ledger_transaction",
            target_id=str(ledger.id),
        )
    await db.commit()
    return {"released": bool(ledger), "ledger_transaction_id": str(ledger.id) if ledger else None}


@router.get("/admin/finance/purchases", response_model=list[PurchaseResponse])
async def admin_purchases(identity: CurrentIdentity, db: Db) -> list[PurchaseResponse]:
    authorize(identity[0], Permission.FINANCIAL_ACCESS)
    rows = (await db.scalars(select(Purchase).order_by(Purchase.created_at.desc()))).all()
    return [purchase_response(row) for row in rows]


@router.post("/admin/finance/purchases/{purchase_id}/refund", response_model=PurchaseResponse)
async def refund(
    purchase_id: UUID, payload: RefundRequest, identity: CurrentIdentity, db: Db
) -> PurchaseResponse:
    authorize(identity[0], Permission.FINANCIAL_ACCESS)
    purchase = await db.get(Purchase, purchase_id)
    if not purchase:
        raise HTTPException(status_code=404, detail="Purchase not found")
    try:
        purchase = await service.refund_purchase(db, purchase, identity[0], payload.reason)
        await db.commit()
        return purchase_response(purchase)
    except service.FinancialError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/admin/finance/subscription-periods/{period_id}/refund", response_model=dict)
async def refund_subscription_period(
    period_id: UUID, payload: RefundRequest, identity: CurrentIdentity, db: Db
) -> dict:
    authorize(identity[0], Permission.FINANCIAL_ACCESS)
    period = await db.get(SubscriptionPeriod, period_id)
    if not period:
        raise HTTPException(status_code=404, detail="Subscription period not found")
    try:
        period = await service.refund_subscription_period(db, period, identity[0], payload.reason)
        await db.commit()
        return {"id": str(period.id), "status": period.status.value}
    except service.FinancialError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/admin/finance/message-unlocks/{purchase_id}/refund", response_model=dict)
async def refund_message_unlock(
    purchase_id: UUID, payload: RefundRequest, identity: CurrentIdentity, db: Db
) -> dict:
    authorize(identity[0], Permission.FINANCIAL_ACCESS)
    purchase = await db.get(MessageUnlockPurchase, purchase_id)
    if not purchase:
        raise HTTPException(404, "Message unlock not found")
    purchase = await service.refund_message_charge(db, purchase, identity[0], payload.reason)
    await db.commit()
    return {"id": str(purchase.id), "status": purchase.status}


@router.post("/admin/finance/message-sends/{send_id}/refund", response_model=dict)
async def refund_message_send(
    send_id: UUID, payload: RefundRequest, identity: CurrentIdentity, db: Db
) -> dict:
    authorize(identity[0], Permission.FINANCIAL_ACCESS)
    pending = await db.get(PendingMessageSend, send_id)
    if not pending:
        raise HTTPException(404, "Message send not found")
    pending = await service.refund_message_charge(db, pending, identity[0], payload.reason)
    await db.commit()
    return {
        "id": str(pending.id),
        "status": pending.status,
        "message_id": str(pending.message_id) if pending.message_id else None,
    }


@router.get("/admin/finance/commission", response_model=dict)
async def commission(identity: CurrentIdentity, db: Db) -> dict:
    authorize(identity[0], Permission.FINANCIAL_ACCESS)
    return {"basis_points": await service.ppv_commission(db)}


@router.put("/admin/finance/commission", response_model=dict)
async def set_commission(payload: CommissionUpdate, identity: CurrentIdentity, db: Db) -> dict:
    authorize(identity[0], Permission.FINANCIAL_CONFIGURE)
    rule = await db.scalar(select(CommissionRule).where(CommissionRule.revenue_type == "ppv"))
    if not rule:
        rule = CommissionRule(revenue_type="ppv", basis_points=payload.basis_points)
        db.add(rule)
    else:
        rule.basis_points = payload.basis_points
    await record_event(
        db,
        "finance.commission_updated",
        actor_user_id=identity[0].id,
        target_type="commission_rule",
        target_id="ppv",
        metadata={"basis_points": payload.basis_points},
    )
    await db.commit()
    return {"basis_points": rule.basis_points}
