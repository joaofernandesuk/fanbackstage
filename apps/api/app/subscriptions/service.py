"""Authoritative subscription pricing and settlement using the Phase 3 ledger."""

import secrets
from calendar import monthrange
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.core.config import get_settings
from app.finance.service import (
    _account,
    commission_amount,
    currency_code,
    post_entries,
    ppv_commission,
)
from app.models.content import ContentEntitlement
from app.models.finance import (
    LedgerAccountKind,
    LedgerDirection,
    LedgerTransactionType,
    PaymentAttempt,
)
from app.models.identity import User
from app.models.subscription import (
    PromotionEligibility,
    PromotionRenewalScope,
    Subscription,
    SubscriptionDuration,
    SubscriptionPeriod,
    SubscriptionPeriodStatus,
    SubscriptionPlan,
    SubscriptionPlanPrice,
    SubscriptionPromotion,
    SubscriptionPromotionRule,
    SubscriptionRenewalAttempt,
    SubscriptionStatus,
)
from app.notifications.service import emit_transactional


class SubscriptionError(ValueError):
    pass


MONTHS = {
    SubscriptionDuration.month_1: 1,
    SubscriptionDuration.month_3: 3,
    SubscriptionDuration.month_6: 6,
    SubscriptionDuration.month_12: 12,
}


def add_months(value: datetime, months: int) -> datetime:
    target = value.month - 1 + months
    year, month = value.year + target // 12, target % 12 + 1
    return value.replace(year=year, month=month, day=min(value.day, monthrange(year, month)[1]))


def discount_amount(base_amount_minor: int, basis_points: int) -> int:
    if base_amount_minor <= 0 or not 0 <= basis_points < 10_000:
        raise SubscriptionError("Invalid subscription discount")
    return base_amount_minor * basis_points // 10_000


async def plan_for_creator(db: AsyncSession, creator_id: UUID) -> SubscriptionPlan | None:
    return await db.scalar(
        select(SubscriptionPlan).where(SubscriptionPlan.creator_id == creator_id)
    )


async def configure_plan(
    db: AsyncSession, creator_id: UUID, currency: str, enabled: bool, prices: list[dict]
) -> SubscriptionPlan:
    currency = currency_code(currency)
    plan = await plan_for_creator(db, creator_id)
    if not plan:
        plan = SubscriptionPlan(creator_id=creator_id, currency=currency, enabled=enabled)
        db.add(plan)
        await db.flush()
    elif plan.currency != currency:
        existing = await db.scalar(
            select(Subscription.id).where(
                Subscription.plan_id == plan.id,
                Subscription.status.in_(
                    [SubscriptionStatus.active, SubscriptionStatus.grace_period]
                ),
            )
        )
        if existing:
            raise SubscriptionError("Cannot change currency while subscriptions are active")
        plan.currency = currency
        plan.enabled = enabled
    else:
        plan.enabled = enabled
    for item in prices:
        duration = SubscriptionDuration(item["duration"])
        amount = int(item["amount_minor"])
        if amount <= 0:
            raise SubscriptionError("Plan prices must be positive")
        price = await db.scalar(
            select(SubscriptionPlanPrice).where(
                SubscriptionPlanPrice.plan_id == plan.id, SubscriptionPlanPrice.duration == duration
            )
        )
        if price:
            price.amount_minor, price.enabled = amount, bool(item["enabled"])
        else:
            db.add(
                SubscriptionPlanPrice(
                    plan_id=plan.id,
                    duration=duration,
                    amount_minor=amount,
                    enabled=bool(item["enabled"]),
                )
            )
    await db.flush()
    return plan


async def _promotion(
    db: AsyncSession,
    buyer: User,
    creator_id: UUID,
    duration: SubscriptionDuration,
    *,
    renewal: bool,
) -> tuple[SubscriptionPromotion | None, int]:
    now = datetime.now(UTC)
    candidates = (
        await db.execute(
            select(SubscriptionPromotion, SubscriptionPromotionRule)
            .join(SubscriptionPromotionRule)
            .where(
                SubscriptionPromotion.creator_id == creator_id,
                SubscriptionPromotion.enabled.is_(True),
                SubscriptionPromotion.start_at <= now,
                (SubscriptionPromotion.end_at.is_(None) | (SubscriptionPromotion.end_at > now)),
                SubscriptionPromotionRule.duration == duration,
            )
        )
    ).all()
    history = (
        await db.scalar(
            select(func.count())
            .select_from(Subscription)
            .where(
                Subscription.subscriber_user_id == buyer.id,
                Subscription.creator_id == creator_id,
                Subscription.status != SubscriptionStatus.pending,
            )
        )
        or 0
    )
    active = await db.scalar(
        select(Subscription.id).where(
            Subscription.subscriber_user_id == buyer.id,
            Subscription.creator_id == creator_id,
            Subscription.status.in_([SubscriptionStatus.active, SubscriptionStatus.grace_period]),
        )
    )
    eligible = []
    for promotion, rule in candidates:
        if renewal and promotion.renewal_scope is not PromotionRenewalScope.initial_and_renewal:
            continue
        if promotion.eligibility is PromotionEligibility.new_subscriber and history:
            continue
        if promotion.eligibility is PromotionEligibility.reactivation and (not history or active):
            continue
        eligible.append((promotion, rule.discount_basis_points))
    return max(eligible, key=lambda item: (item[1], str(item[0].id)), default=(None, 0))


async def create_subscription(
    db: AsyncSession, buyer: User, creator_id: UUID, duration_value: str, idempotency_key: str
) -> Subscription:
    duration = SubscriptionDuration(duration_value)
    if not idempotency_key:
        raise SubscriptionError("A valid Idempotency-Key is required")
    existing = await db.scalar(
        select(Subscription)
        .join(SubscriptionPeriod)
        .join(PaymentAttempt)
        .where(
            PaymentAttempt.buyer_user_id == buyer.id,
            PaymentAttempt.idempotency_key == idempotency_key,
        )
    )
    if existing:
        return existing
    active = await db.scalar(
        select(Subscription)
        .where(
            Subscription.subscriber_user_id == buyer.id,
            Subscription.creator_id == creator_id,
            Subscription.status.in_(
                [
                    SubscriptionStatus.pending,
                    SubscriptionStatus.active,
                    SubscriptionStatus.grace_period,
                    SubscriptionStatus.payment_failed,
                ]
            ),
        )
        .with_for_update()
    )
    if active:
        raise SubscriptionError("An existing subscription must be managed instead")
    plan = await plan_for_creator(db, creator_id)
    if not plan or not plan.enabled:
        raise SubscriptionError("Subscriptions are unavailable")
    price = await db.scalar(
        select(SubscriptionPlanPrice).where(
            SubscriptionPlanPrice.plan_id == plan.id,
            SubscriptionPlanPrice.duration == duration,
            SubscriptionPlanPrice.enabled.is_(True),
        )
    )
    if not price:
        raise SubscriptionError("This subscription duration is unavailable")
    promotion, bps = await _promotion(db, buyer, creator_id, duration, renewal=False)
    discount = discount_amount(price.amount_minor, bps)
    charged = price.amount_minor - discount
    fee, creator_amount = commission_amount(charged, await ppv_commission(db))
    now = datetime.now(UTC)
    subscription = Subscription(
        subscriber_user_id=buyer.id,
        creator_id=creator_id,
        plan_id=plan.id,
        duration=duration,
        currency=plan.currency,
    )
    db.add(subscription)
    await db.flush()
    attempt = PaymentAttempt(
        buyer_user_id=buyer.id,
        provider=get_settings().payment_provider,
        provider_reference=f"devsub_{secrets.token_urlsafe(18)}",
        amount_minor=charged,
        currency=plan.currency,
        idempotency_key=idempotency_key,
    )
    db.add(attempt)
    await db.flush()
    period = SubscriptionPeriod(
        subscription_id=subscription.id,
        sequence=1,
        period_start=now,
        period_end=add_months(now, MONTHS[duration]),
        duration=duration,
        base_amount_minor=price.amount_minor,
        discount_amount_minor=discount,
        charged_amount_minor=charged,
        currency=plan.currency,
        promotion_id=promotion.id if promotion else None,
        promotion_eligibility=promotion.eligibility if promotion else None,
        discount_basis_points=bps,
        commission_basis_points=await ppv_commission(db),
        platform_fee_minor=fee,
        creator_amount_minor=creator_amount,
        payment_attempt_id=attempt.id,
    )
    db.add(period)
    await db.flush()
    return subscription


async def settle_payment_attempt(db: AsyncSession, attempt: PaymentAttempt) -> Subscription | None:
    period = await db.scalar(
        select(SubscriptionPeriod)
        .outerjoin(
            SubscriptionRenewalAttempt,
            SubscriptionRenewalAttempt.subscription_period_id == SubscriptionPeriod.id,
        )
        .where(
            (SubscriptionPeriod.payment_attempt_id == attempt.id)
            | (SubscriptionRenewalAttempt.payment_attempt_id == attempt.id)
        )
        .with_for_update(of=SubscriptionPeriod)
    )
    if not period:
        return None
    subscription = await db.scalar(
        select(Subscription).where(Subscription.id == period.subscription_id).with_for_update()
    )
    assert subscription
    if period.status is SubscriptionPeriodStatus.active:
        return subscription
    clearing = await _account(db, LedgerAccountKind.platform_clearing, period.currency)
    revenue = await _account(db, LedgerAccountKind.platform_revenue, period.currency)
    from app.finance.service import creator_revenue_allocation
    from app.referrals.service import record_revenue_allocation, revenue_allocation

    event_at = attempt.completed_at or period.created_at
    allocation_entries, allocation_metadata = await creator_revenue_allocation(
        db,
        subscription.creator_id,
        period.currency,
        period.creator_amount_minor,
        event_at,
    )
    referral_entries, referral_allocation = await revenue_allocation(
        db,
        buyer_user_id=subscription.subscriber_user_id,
        revenue_type="subscription",
        currency=period.currency,
        platform_fee_minor=period.platform_fee_minor,
        occurred_at=event_at,
    )
    referral_amount = int(referral_allocation["amount_minor"]) if referral_allocation else 0
    ledger = await post_entries(
        db,
        transaction_type=LedgerTransactionType.subscription_charge,
        currency=period.currency,
        idempotency_key=f"subscription-period:{period.id}",
        reference=f"subscription_period:{period.id}",
        entries=[
            (clearing, LedgerDirection.debit, period.charged_amount_minor),
            (revenue, LedgerDirection.credit, period.platform_fee_minor - referral_amount),
            *referral_entries,
            *allocation_entries,
        ],
        metadata={
            "subscription_id": str(subscription.id),
            "period_id": str(period.id),
            "platform_fee_minor": str(period.platform_fee_minor),
            "referral_amount_minor": str(referral_amount),
            **allocation_metadata,
        },
    )
    await record_revenue_allocation(
        db,
        source_ledger_transaction_id=ledger.id,
        allocation=referral_allocation,
    )
    entitlement = (
        await db.get(ContentEntitlement, period.entitlement_id) if period.entitlement_id else None
    )
    if not entitlement:
        entitlement = ContentEntitlement(
            subject_user_id=subscription.subscriber_user_id,
            creator_id=subscription.creator_id,
            source_type="subscription_period",
            source_reference=str(period.id),
            valid_from=period.period_start,
            valid_until=period.period_end,
        )
        db.add(entitlement)
        await db.flush()
    period.status, period.ledger_transaction_id, period.entitlement_id = (
        SubscriptionPeriodStatus.active,
        ledger.id,
        entitlement.id,
    )
    (
        subscription.status,
        subscription.current_period_start,
        subscription.current_period_end,
        subscription.grace_period_end,
    ) = SubscriptionStatus.active, period.period_start, period.period_end, None
    await record_event(
        db,
        "subscription.period_settled",
        actor_user_id=subscription.subscriber_user_id,
        target_type="subscription",
        target_id=str(subscription.id),
        metadata={"period_id": str(period.id)},
    )
    await emit_transactional(
        db,
        recipient_user_id=subscription.subscriber_user_id,
        notification_type="SUBSCRIPTION_RENEWED" if period.sequence > 1 else "SUBSCRIPTION_STARTED",
        source_domain="subscriptions",
        source_id=str(period.id),
        title="Subscription confirmed",
        body="Your subscription payment has been confirmed.",
        target_path="/subscriptions",
    )
    return subscription


async def set_auto_renew(
    db: AsyncSession, subscriber: User, subscription_id: UUID, enabled: bool
) -> Subscription:
    subscription = await db.scalar(
        select(Subscription)
        .where(Subscription.id == subscription_id, Subscription.subscriber_user_id == subscriber.id)
        .with_for_update()
    )
    if not subscription or subscription.status not in {
        SubscriptionStatus.active,
        SubscriptionStatus.grace_period,
    }:
        raise SubscriptionError("Subscription cannot be managed")
    subscription.auto_renew, subscription.cancel_at_period_end = enabled, not enabled
    subscription.cancelled_at = None if enabled else datetime.now(UTC)
    await record_event(
        db,
        "subscription.auto_renew_changed",
        actor_user_id=subscriber.id,
        target_type="subscription",
        target_id=str(subscription.id),
        metadata={"auto_renew": enabled},
    )
    if not enabled:
        await emit_transactional(
            db,
            recipient_user_id=subscription.subscriber_user_id,
            notification_type="SUBSCRIPTION_CANCELLED",
            source_domain="subscriptions",
            source_id=str(subscription.id),
            title="Subscription cancellation scheduled",
            body="Your subscription will not renew automatically.",
            target_path="/subscriptions",
        )
    return subscription


async def fail_payment_attempt(db: AsyncSession, attempt: PaymentAttempt) -> Subscription | None:
    """Record a failed initial/renewal charge without extending entitlement."""
    period = await db.scalar(
        select(SubscriptionPeriod)
        .outerjoin(
            SubscriptionRenewalAttempt,
            SubscriptionRenewalAttempt.subscription_period_id == SubscriptionPeriod.id,
        )
        .where(
            (SubscriptionPeriod.payment_attempt_id == attempt.id)
            | (SubscriptionRenewalAttempt.payment_attempt_id == attempt.id)
        )
        .with_for_update(of=SubscriptionPeriod)
    )
    if not period:
        return None
    subscription = await db.scalar(
        select(Subscription).where(Subscription.id == period.subscription_id).with_for_update()
    )
    assert subscription
    period.status = SubscriptionPeriodStatus.failed
    renewal_attempt = await db.scalar(
        select(SubscriptionRenewalAttempt).where(
            SubscriptionRenewalAttempt.payment_attempt_id == attempt.id
        )
    )
    if renewal_attempt:
        renewal_attempt.next_retry_at = datetime.now(UTC) + timedelta(
            seconds=get_settings().subscription_renewal_retry_seconds
        )
    if subscription.current_period_end and subscription.current_period_end <= datetime.now(UTC):
        subscription.status = SubscriptionStatus.grace_period
        subscription.grace_period_end = subscription.grace_period_end or datetime.now(
            UTC
        ) + timedelta(days=get_settings().subscription_grace_period_days)
        last_paid_period = await db.scalar(
            select(SubscriptionPeriod)
            .where(
                SubscriptionPeriod.subscription_id == subscription.id,
                SubscriptionPeriod.status == SubscriptionPeriodStatus.active,
                SubscriptionPeriod.entitlement_id.is_not(None),
            )
            .order_by(SubscriptionPeriod.sequence.desc())
        )
        if last_paid_period and last_paid_period.entitlement_id:
            entitlement = await db.get(ContentEntitlement, last_paid_period.entitlement_id)
            if entitlement:
                entitlement.valid_until = subscription.grace_period_end
        await record_event(
            db,
            "subscription.grace_entered",
            actor_user_id=subscription.subscriber_user_id,
            target_type="subscription",
            target_id=str(subscription.id),
            metadata={"period_id": str(period.id)},
        )
    else:
        subscription.status = SubscriptionStatus.payment_failed
    await emit_transactional(
        db,
        recipient_user_id=subscription.subscriber_user_id,
        notification_type="SUBSCRIPTION_PAYMENT_FAILED",
        source_domain="subscriptions",
        source_id=str(period.id),
        title="Subscription payment failed",
        body="Update your payment method to continue your subscription.",
        target_path="/subscriptions",
    )
    return subscription


async def finalize_expired_subscriptions(db: AsyncSession) -> int:
    now = datetime.now(UTC)
    rows = (
        await db.scalars(
            select(Subscription)
            .where(
                (Subscription.status == SubscriptionStatus.grace_period)
                & (Subscription.grace_period_end <= now)
                | (
                    (Subscription.status == SubscriptionStatus.active)
                    & (Subscription.auto_renew.is_(False))
                    & (Subscription.current_period_end <= now)
                )
            )
            .with_for_update(skip_locked=True)
        )
    ).all()
    for subscription in rows:
        subscription.status, subscription.ended_at = SubscriptionStatus.expired, now
        entitlement_ids = (
            await db.scalars(
                select(SubscriptionPeriod.entitlement_id).where(
                    SubscriptionPeriod.subscription_id == subscription.id,
                    SubscriptionPeriod.entitlement_id.is_not(None),
                )
            )
        ).all()
        for entitlement_id in entitlement_ids:
            entitlement = await db.get(ContentEntitlement, entitlement_id)
            if entitlement and (entitlement.valid_until is None or entitlement.valid_until > now):
                entitlement.valid_until = now
        await record_event(
            db,
            "subscription.expired",
            actor_user_id=subscription.subscriber_user_id,
            target_type="subscription",
            target_id=str(subscription.id),
        )
    return len(rows)


async def renew_due_subscriptions(db: AsyncSession) -> int:
    now = datetime.now(UTC)
    due = (
        await db.scalars(
            select(Subscription)
            .where(
                Subscription.status == SubscriptionStatus.active,
                Subscription.auto_renew.is_(True),
                Subscription.cancel_at_period_end.is_(False),
                Subscription.current_period_end <= now,
            )
            .with_for_update(skip_locked=True)
        )
    ).all()
    created = 0
    for subscription in due:
        plan = await db.get(SubscriptionPlan, subscription.plan_id)
        price = await db.scalar(
            select(SubscriptionPlanPrice).where(
                SubscriptionPlanPrice.plan_id == subscription.plan_id,
                SubscriptionPlanPrice.duration == subscription.duration,
                SubscriptionPlanPrice.enabled.is_(True),
            )
        )
        buyer = await db.get(User, subscription.subscriber_user_id)
        if not plan or not plan.enabled or not price or not buyer:
            subscription.status, subscription.auto_renew = SubscriptionStatus.expired, False
            continue
        sequence = (
            int(
                await db.scalar(
                    select(func.coalesce(func.max(SubscriptionPeriod.sequence), 0)).where(
                        SubscriptionPeriod.subscription_id == subscription.id
                    )
                )
                or 0
            )
            + 1
        )
        promotion, bps = await _promotion(
            db, buyer, subscription.creator_id, subscription.duration, renewal=True
        )
        discount, charged = (
            discount_amount(price.amount_minor, bps),
            price.amount_minor - discount_amount(price.amount_minor, bps),
        )
        fee, creator_amount = commission_amount(charged, await ppv_commission(db))
        attempt = PaymentAttempt(
            buyer_user_id=buyer.id,
            provider=get_settings().payment_provider,
            provider_reference=f"devrenew_{secrets.token_urlsafe(18)}",
            amount_minor=charged,
            currency=subscription.currency,
            idempotency_key=f"renewal:{subscription.id}:{sequence}",
        )
        db.add(attempt)
        await db.flush()
        db.add(
            SubscriptionPeriod(
                subscription_id=subscription.id,
                sequence=sequence,
                period_start=subscription.current_period_end,
                period_end=add_months(
                    subscription.current_period_end, MONTHS[subscription.duration]
                ),
                duration=subscription.duration,
                base_amount_minor=price.amount_minor,
                discount_amount_minor=discount,
                charged_amount_minor=charged,
                currency=subscription.currency,
                promotion_id=promotion.id if promotion else None,
                promotion_eligibility=promotion.eligibility if promotion else None,
                discount_basis_points=bps,
                commission_basis_points=await ppv_commission(db),
                platform_fee_minor=fee,
                creator_amount_minor=creator_amount,
                payment_attempt_id=attempt.id,
            )
        )
        await db.flush()
        period = await db.scalar(
            select(SubscriptionPeriod).where(SubscriptionPeriod.payment_attempt_id == attempt.id)
        )
        assert period
        db.add(
            SubscriptionRenewalAttempt(
                subscription_period_id=period.id,
                payment_attempt_id=attempt.id,
                attempt_number=1,
                next_retry_at=None,
            )
        )
        subscription.status = SubscriptionStatus.payment_failed
        created += 1
    return created


async def retry_failed_subscription_renewals(db: AsyncSession) -> int:
    now, settings = datetime.now(UTC), get_settings()
    periods = (
        await db.scalars(
            select(SubscriptionPeriod)
            .join(Subscription)
            .where(
                Subscription.status == SubscriptionStatus.grace_period,
                Subscription.auto_renew.is_(True),
                Subscription.grace_period_end > now,
                SubscriptionPeriod.status == SubscriptionPeriodStatus.failed,
            )
            .with_for_update(skip_locked=True)
        )
    ).all()
    created = 0
    for period in periods:
        count = int(
            await db.scalar(
                select(func.count())
                .select_from(SubscriptionRenewalAttempt)
                .where(SubscriptionRenewalAttempt.subscription_period_id == period.id)
            )
            or 1
        )
        if count >= settings.subscription_renewal_retry_limit:
            continue
        latest = await db.scalar(
            select(SubscriptionRenewalAttempt)
            .where(SubscriptionRenewalAttempt.subscription_period_id == period.id)
            .order_by(SubscriptionRenewalAttempt.attempt_number.desc())
        )
        if latest and latest.next_retry_at and latest.next_retry_at > now:
            continue
        subscription = await db.get(Subscription, period.subscription_id)
        assert subscription
        attempt = PaymentAttempt(
            buyer_user_id=subscription.subscriber_user_id,
            provider=settings.payment_provider,
            provider_reference=f"devretry_{secrets.token_urlsafe(18)}",
            amount_minor=period.charged_amount_minor,
            currency=period.currency,
            idempotency_key=f"renewal-retry:{period.id}:{count + 1}",
        )
        db.add(attempt)
        await db.flush()
        db.add(
            SubscriptionRenewalAttempt(
                subscription_period_id=period.id,
                payment_attempt_id=attempt.id,
                attempt_number=count + 1,
                next_retry_at=now + timedelta(seconds=settings.subscription_renewal_retry_seconds),
            )
        )
        period.status = SubscriptionPeriodStatus.pending
        created += 1
    return created
