from uuid import UUID

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db, OptionalIdentity
from app.audit.service import record_event
from app.finance import service as finance
from app.media.service import approved_creator
from app.models.creator import CreatorProfile
from app.models.subscription import (
    PromotionEligibility,
    PromotionRenewalScope,
    Subscription,
    SubscriptionPeriod,
    SubscriptionPlanPrice,
    SubscriptionPromotion,
    SubscriptionPromotionRule,
)
from app.schemas.subscription import (
    AutoRenewInput,
    PlanInput,
    PromotionInput,
    PublicPlanResponse,
    SubscriptionResponse,
    SubscriptionStart,
)
from app.subscriptions import service

router = APIRouter(tags=["subscriptions"])


def response(subscription: Subscription) -> SubscriptionResponse:
    return SubscriptionResponse(
        id=subscription.id,
        creator_id=subscription.creator_id,
        duration=subscription.duration.value,
        status=subscription.status.value,
        currency=subscription.currency,
        auto_renew=subscription.auto_renew,
        cancel_at_period_end=subscription.cancel_at_period_end,
        current_period_end=subscription.current_period_end,
    )


@router.put("/creator/subscription-plan", response_model=dict)
async def set_plan(payload: PlanInput, identity: CurrentIdentity, db: Db) -> dict:
    creator = await approved_creator(db, identity[0])
    try:
        plan = await service.configure_plan(
            db,
            creator.id,
            payload.currency,
            payload.enabled,
            [item.model_dump() for item in payload.prices],
        )
        await record_event(
            db,
            "subscription.plan_updated",
            actor_user_id=identity[0].id,
            target_type="subscription_plan",
            target_id=str(plan.id),
        )
        await db.commit()
    except (service.SubscriptionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc
    return {"id": str(plan.id), "currency": plan.currency, "enabled": plan.enabled}


@router.post("/creator/subscription-promotions", response_model=dict)
async def create_promotion(payload: PromotionInput, identity: CurrentIdentity, db: Db) -> dict:
    creator = await approved_creator(db, identity[0])
    try:
        promotion = SubscriptionPromotion(
            creator_id=creator.id,
            name=payload.name,
            eligibility=PromotionEligibility(payload.eligibility),
            renewal_scope=PromotionRenewalScope(payload.renewal_scope),
            enabled=payload.enabled,
            start_at=payload.start_at,
            end_at=payload.end_at,
        )
        db.add(promotion)
        await db.flush()
        db.add_all(
            SubscriptionPromotionRule(
                promotion_id=promotion.id,
                duration=rule.duration,
                discount_basis_points=rule.discount_basis_points,
            )
            for rule in payload.rules
        )
        await record_event(
            db,
            "subscription.promotion_created",
            actor_user_id=identity[0].id,
            target_type="subscription_promotion",
            target_id=str(promotion.id),
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(400, "Invalid promotion") from exc
    return {"id": str(promotion.id)}


@router.get("/creators/{username}/subscription-options", response_model=list[PublicPlanResponse])
async def public_options(
    username: str, db: Db, identity: OptionalIdentity
) -> list[PublicPlanResponse]:
    creator = await db.scalar(select(CreatorProfile).where(CreatorProfile.username == username))
    if not creator:
        raise HTTPException(404, "Creator not found")
    plan = await service.plan_for_creator(db, creator.id)
    if not plan or not plan.enabled:
        return []
    buyer = identity[0] if identity else None
    prices = (
        await db.scalars(
            select(SubscriptionPlanPrice).where(
                SubscriptionPlanPrice.plan_id == plan.id, SubscriptionPlanPrice.enabled.is_(True)
            )
        )
    ).all()
    options = []
    for price in prices:
        _promotion, bps = (
            (await service._promotion(db, buyer, creator.id, price.duration, renewal=False))
            if buyer
            else (None, 0)
        )
        discount = service.discount_amount(price.amount_minor, bps)
        options.append(
            PublicPlanResponse(
                duration=price.duration.value,
                base_amount_minor=price.amount_minor,
                effective_amount_minor=price.amount_minor - discount,
                currency=plan.currency,
                discount_basis_points=bps,
            )
        )
    return options


@router.post("/subscriptions/creator/{creator_id}", response_model=SubscriptionResponse)
async def start(
    creator_id: UUID,
    payload: SubscriptionStart,
    identity: CurrentIdentity,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> SubscriptionResponse:
    try:
        subscription = await service.create_subscription(
            db, identity[0], creator_id, payload.duration, idempotency_key or ""
        )
        await db.commit()
        return response(subscription)
    except (service.SubscriptionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.post(
    "/subscriptions/payments/development/{attempt_id}/complete", response_model=SubscriptionResponse
)
async def complete(attempt_id: UUID, identity: CurrentIdentity, db: Db) -> SubscriptionResponse:
    attempt = await db.get(finance.PaymentAttempt, attempt_id)
    if not attempt or attempt.buyer_user_id != identity[0].id:
        raise HTTPException(404, "Payment attempt not found")
    payload, signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db, payload, signature)
    subscription = await service.settle_payment_attempt(db, attempt)
    await db.commit()
    if not subscription:
        raise HTTPException(404, "Subscription not found")
    return response(subscription)


@router.post(
    "/subscriptions/{subscription_id}/complete-development", response_model=SubscriptionResponse
)
async def complete_subscription(
    subscription_id: UUID, identity: CurrentIdentity, db: Db
) -> SubscriptionResponse:
    period = await db.scalar(
        select(SubscriptionPeriod)
        .join(Subscription)
        .where(
            SubscriptionPeriod.subscription_id == subscription_id,
            Subscription.subscriber_user_id == identity[0].id,
            SubscriptionPeriod.status == "pending",
        )
    )
    if not period:
        raise HTTPException(404, "Pending subscription payment not found")
    attempt = await db.get(finance.PaymentAttempt, period.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db, payload, signature)
    subscription = await service.settle_payment_attempt(db, attempt)
    await db.commit()
    assert subscription
    return response(subscription)


@router.get("/subscriptions/mine", response_model=list[SubscriptionResponse])
async def mine(identity: CurrentIdentity, db: Db) -> list[SubscriptionResponse]:
    return [
        response(item)
        for item in (
            await db.scalars(
                select(Subscription)
                .where(Subscription.subscriber_user_id == identity[0].id)
                .order_by(Subscription.created_at.desc())
            )
        ).all()
    ]


@router.patch("/subscriptions/{subscription_id}/auto-renew", response_model=SubscriptionResponse)
async def auto_renew(
    subscription_id: UUID, payload: AutoRenewInput, identity: CurrentIdentity, db: Db
) -> SubscriptionResponse:
    try:
        subscription = await service.set_auto_renew(
            db, identity[0], subscription_id, payload.enabled
        )
        await db.commit()
        return response(subscription)
    except service.SubscriptionError as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc
