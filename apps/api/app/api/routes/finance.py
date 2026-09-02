from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db
from app.audit.service import record_event
from app.compliance.http import resolve_request_compliance_decision
from app.featuring.service import booking_for_payment_attempt
from app.finance import operations, service
from app.media.service import approved_creator
from app.models.compliance import ComplianceFeature
from app.models.content import ContentItem
from app.models.creator import CreatorProfile
from app.models.finance import (
    CommissionRule,
    PaymentAttempt,
    PaymentStatus,
    Purchase,
    StagingPaymentSandboxEvent,
)
from app.models.marketplace import MarketplaceOrder
from app.models.messaging import MessageUnlockPurchase, PendingMessageSend
from app.models.notification import NotificationIntent
from app.models.streaming import LiveCommerceCharge, PrivateSession
from app.models.subscription import SubscriptionPeriod
from app.permissions.policies import Permission, authorize
from app.schemas.finance import (
    CommissionUpdate,
    CreatorEarningsResponse,
    DevelopmentPaymentCompletionResponse,
    FinanceReconciliationInput,
    FinanceRefundOperationInput,
    PaymentCheckoutResponse,
    PurchaseHistoryResponse,
    PurchaseResponse,
    RefundRequest,
    StagingPaymentCheckoutInput,
)

router = APIRouter(tags=["finance"])


@router.get("/admin/finance/operations")
async def finance_operations(
    identity: CurrentIdentity,
    db: Db,
    search: str | None = None,
    creator: str | None = None,
    provider: str | None = None,
    status: str | None = None,
    currency: str | None = None,
    source_domain: str | None = None,
    refund_state: str | None = None,
    exceptions: bool = False,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict:
    authorize(identity[0], Permission.FINANCIAL_ACCESS)
    try:
        resolved_status = PaymentStatus(status) if status else None
        return await operations.search_payments(
            db,
            search=search,
            creator=creator,
            provider=provider,
            status=resolved_status,
            currency=currency,
            source_domain=source_domain,
            refund_state=refund_state,
            exceptions_only=exceptions,
            starts_at=starts_at,
            ends_at=ends_at,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/admin/finance/operations/exceptions")
async def finance_exception_counts(identity: CurrentIdentity, db: Db) -> dict:
    authorize(identity[0], Permission.FINANCIAL_ACCESS)
    return await operations.exception_counts(db)


@router.get("/admin/finance/operations/{payment_attempt_id}")
async def finance_operation_detail(
    payment_attempt_id: UUID, identity: CurrentIdentity, db: Db
) -> dict:
    authorize(identity[0], Permission.FINANCIAL_ACCESS)
    detail = await operations.payment_detail(db, payment_attempt_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Payment record not found")
    try:
        authorize(identity[0], Permission.FINANCIAL_AUDIT)
    except HTTPException:
        detail["audit"] = []
    return detail


@router.post("/admin/finance/operations/{payment_attempt_id}/refund", status_code=202)
async def request_finance_refund(
    payment_attempt_id: UUID,
    payload: FinanceRefundOperationInput,
    identity: CurrentIdentity,
    db: Db,
) -> dict:
    authorize(identity[0], Permission.FINANCIAL_REFUND)
    if not payload.confirmed:
        raise HTTPException(status_code=400, detail="Refund confirmation is required")
    attempt = await db.get(PaymentAttempt, payment_attempt_id, with_for_update=True)
    if attempt is None:
        raise HTTPException(status_code=404, detail="Payment record not found")
    if attempt.status in {PaymentStatus.refunded, PaymentStatus.chargeback}:
        return {"id": str(attempt.id), "status": attempt.status.value, "queued": False}
    if attempt.provider != "staging_sandbox":
        raise HTTPException(
            status_code=409,
            detail="Provider refund commands are unavailable until its adapter supports them",
        )
    if attempt.status not in {PaymentStatus.succeeded, PaymentStatus.disputed}:
        raise HTTPException(
            status_code=409, detail="Only settled or disputed payments can be refunded"
        )
    try:
        existing = await db.scalar(
            select(StagingPaymentSandboxEvent).where(
                StagingPaymentSandboxEvent.payment_attempt_id == attempt.id,
                StagingPaymentSandboxEvent.event_type == "payment.refunded",
            )
        )
        if existing is not None:
            return {"id": str(attempt.id), "status": attempt.status.value, "queued": False}
        event = await service.staging_checkout(db, attempt, outcome="REFUND")
        await record_event(
            db,
            "finance.refund_requested",
            actor_user_id=identity[0].id,
            target_type="payment_attempt",
            target_id=str(attempt.id),
            metadata={
                "provider": attempt.provider,
                "provider_event_id": event.external_event_id,
                "reason": payload.reason,
            },
        )
        await db.commit()
        return {"id": str(attempt.id), "status": attempt.status.value, "queued": True}
    except service.FinancialError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/admin/finance/reconciliation")
async def reconcile_finance_operations(
    payload: FinanceReconciliationInput, identity: CurrentIdentity, db: Db
) -> dict:
    authorize(identity[0], Permission.FINANCIAL_RECONCILE)
    if not payload.confirmed:
        raise HTTPException(status_code=400, detail="Reconciliation confirmation is required")
    reconciled = await service.reconcile_succeeded_payments(db, limit=payload.limit)
    await record_event(
        db,
        "finance.reconciliation_requested",
        actor_user_id=identity[0].id,
        target_type="payment_operations",
        target_id="bounded_batch",
        metadata={"limit": payload.limit, "reconciled": reconciled},
    )
    await db.commit()
    return {"reconciled": reconciled}


async def request_ppv_decisions(db: Db, request: Request, user):
    return {
        feature: await resolve_request_compliance_decision(
            db,
            request,
            user=user,
            feature=feature,
            adult_restricted=True,
        )
        for feature in (ComplianceFeature.ppv, ComplianceFeature.purchases)
    }


def financial_error_detail(exc: service.FinancialError):
    decision = exc.compliance_decision
    if decision is None:
        return str(exc)
    return {
        "message": str(exc),
        "code": decision.code,
        "action": decision.action,
        "reason": decision.reason,
    }


async def dispatch_purchase_receipt(db: Db, purchase: Purchase | None) -> None:
    """Queue the durable receipt only after its financial transaction committed."""
    if purchase is None:
        return
    intent = await db.scalar(
        select(NotificationIntent).where(
            NotificationIntent.notification_type == "PURCHASE_RECEIPT",
            NotificationIntent.source_domain == "finance",
            NotificationIntent.source_id == str(purchase.id),
            NotificationIntent.recipient_user_id == purchase.buyer_user_id,
        )
    )
    if intent:
        from app.worker.tasks import deliver_notification

        deliver_notification.delay(str(intent.id))


def purchase_response(purchase: Purchase) -> PurchaseResponse:
    return PurchaseResponse(
        id=purchase.id,
        content_id=purchase.content_id,
        status=purchase.status.value,
        gross_amount_minor=purchase.gross_amount_minor,
        platform_fee_minor=purchase.platform_fee_minor,
        creator_amount_minor=purchase.creator_amount_minor,
        currency=purchase.currency,
        payment_attempt_id=service.response_payment_attempt_id(purchase),
    )


@router.post("/purchases/content/{content_id}", response_model=PurchaseResponse)
async def start_purchase(
    content_id: UUID,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> PurchaseResponse:
    try:
        purchase = await service.initiate_purchase(
            db,
            identity[0],
            content_id,
            idempotency_key or "",
            compliance_decisions=await request_ppv_decisions(db, request, identity[0]),
        )
        await db.commit()
        return purchase_response(purchase)
    except service.FinancialError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if exc.compliance_decision else 400,
            detail=financial_error_detail(exc),
        ) from exc


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


@router.post(
    "/payments/development/{payment_attempt_id}/complete",
    response_model=PurchaseResponse | DevelopmentPaymentCompletionResponse,
)
async def complete_development_payment(
    payment_attempt_id: UUID, identity: CurrentIdentity, db: Db
) -> PurchaseResponse | DevelopmentPaymentCompletionResponse:
    attempt = await db.get(PaymentAttempt, payment_attempt_id)
    if not attempt or attempt.buyer_user_id != identity[0].id:
        raise HTTPException(status_code=404, detail="Payment attempt not found")
    if attempt.provider != "development":
        raise HTTPException(status_code=404, detail="Development payment is unavailable")
    payload, signature = service.development_webhook_payload(attempt)
    try:
        purchase = await service.process_development_webhook(db, payload, signature)
        await db.commit()
        await dispatch_purchase_receipt(db, purchase)
    except service.FinancialError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if purchase is not None:
        return purchase_response(purchase)

    purchase = await db.scalar(select(Purchase).where(Purchase.payment_attempt_id == attempt.id))
    if purchase is not None:
        return purchase_response(purchase)

    booking = await booking_for_payment_attempt(db, attempt.id)
    if booking is not None:
        return DevelopmentPaymentCompletionResponse(
            id=booking.id, status=booking.status.value, payment_attempt_id=attempt.id
        )

    unlock = await db.scalar(
        select(MessageUnlockPurchase).where(MessageUnlockPurchase.payment_attempt_id == attempt.id)
    )
    if unlock is not None:
        return DevelopmentPaymentCompletionResponse(
            id=unlock.id, status=unlock.status, payment_attempt_id=attempt.id
        )
    pending_send = await db.scalar(
        select(PendingMessageSend).where(PendingMessageSend.payment_attempt_id == attempt.id)
    )
    if pending_send is not None:
        return DevelopmentPaymentCompletionResponse(
            id=pending_send.id, status=pending_send.status, payment_attempt_id=attempt.id
        )
    private_session = await db.scalar(
        select(PrivateSession).where(PrivateSession.payment_attempt_id == attempt.id)
    )
    if private_session is not None:
        return DevelopmentPaymentCompletionResponse(
            id=private_session.id,
            status=private_session.status.value,
            payment_attempt_id=attempt.id,
        )
    live_charge = await db.scalar(
        select(LiveCommerceCharge).where(LiveCommerceCharge.payment_attempt_id == attempt.id)
    )
    if live_charge is not None:
        return DevelopmentPaymentCompletionResponse(
            id=live_charge.id,
            status=live_charge.status.value,
            payment_attempt_id=attempt.id,
        )
    order = await db.scalar(
        select(MarketplaceOrder).where(MarketplaceOrder.payment_attempt_id == attempt.id)
    )
    if order is not None:
        return DevelopmentPaymentCompletionResponse(
            id=order.id, status=order.status.value, payment_attempt_id=attempt.id
        )
    raise HTTPException(status_code=409, detail="Payment settlement was not found")


@router.post("/payments/webhooks/development", status_code=204)
async def development_webhook(request: Request, db: Db) -> None:
    payload = await request.body()
    try:
        purchase = await service.process_development_webhook(
            db, payload, request.headers.get("X-Payment-Signature")
        )
        await db.commit()
        await dispatch_purchase_receipt(db, purchase)
    except service.FinancialError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/payments/{payment_attempt_id}/checkout", response_model=PaymentCheckoutResponse)
async def payment_checkout(
    payment_attempt_id: UUID, identity: CurrentIdentity, db: Db
) -> PaymentCheckoutResponse:
    attempt = await db.get(PaymentAttempt, payment_attempt_id)
    if not attempt or attempt.buyer_user_id != identity[0].id:
        raise HTTPException(status_code=404, detail="Payment attempt not found")
    try:
        checkout = service.payment_provider().create_checkout(attempt)
    except service.PaymentProviderError as exc:
        raise HTTPException(status_code=409, detail="Payment checkout is unavailable") from exc
    return PaymentCheckoutResponse(
        payment_attempt_id=attempt.id,
        provider=attempt.provider,
        provider_reference=checkout.provider_reference,
        action=checkout.action,
        status=attempt.status.value,
    )


@router.post("/payments/staging-sandbox/{payment_attempt_id}/checkout", status_code=202)
async def staging_payment_checkout(
    payment_attempt_id: UUID,
    payload: StagingPaymentCheckoutInput,
    identity: CurrentIdentity,
    db: Db,
) -> dict[str, str]:
    """Staging-only checkout UI boundary; this only queues a signed callback."""
    attempt = await db.get(PaymentAttempt, payment_attempt_id, with_for_update=True)
    if not attempt or attempt.buyer_user_id != identity[0].id:
        raise HTTPException(status_code=404, detail="Payment attempt not found")
    if attempt.provider != "staging_sandbox":
        raise HTTPException(status_code=404, detail="Staging payment sandbox is unavailable")
    try:
        event = await service.staging_checkout(
            db,
            attempt,
            outcome="SUCCESS" if payload.outcome == "DELAYED_SUCCESS" else payload.outcome,
            delayed=payload.outcome == "DELAYED_SUCCESS",
        )
        await db.commit()
    except service.FinancialError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "queued", "event_id": event.external_event_id}


@router.post("/payments/webhooks/staging-sandbox", status_code=204)
async def staging_payment_webhook(request: Request, db: Db) -> None:
    if request.headers.get("content-length") and int(request.headers["content-length"]) > 65536:
        raise HTTPException(status_code=413, detail="Webhook payload is too large")
    payload = await request.body()
    if len(payload) > 65536:
        raise HTTPException(status_code=413, detail="Webhook payload is too large")
    try:
        purchase = await service.process_payment_webhook(
            db, "staging_sandbox", payload, request.headers.get("X-Payment-Signature")
        )
        await db.commit()
        await dispatch_purchase_receipt(db, purchase)
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
    authorize(identity[0], Permission.FINANCIAL_REFUND)
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
    authorize(identity[0], Permission.FINANCIAL_REFUND)
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
    authorize(identity[0], Permission.FINANCIAL_REFUND)
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
    authorize(identity[0], Permission.FINANCIAL_REFUND)
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
