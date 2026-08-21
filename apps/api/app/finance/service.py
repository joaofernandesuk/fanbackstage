"""Financial domain services. All value movement is posted as immutable ledger entries."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.core.config import get_settings
from app.finance.providers import PaymentProviderError, payment_provider
from app.models.content import (
    AccessPolicy,
    ContentEntitlement,
    ContentItem,
    ContentStatus,
    EntitlementStatus,
    ModerationStatus,
)
from app.models.finance import (
    CommissionRule,
    LedgerAccount,
    LedgerAccountKind,
    LedgerDirection,
    LedgerEntry,
    LedgerTransaction,
    LedgerTransactionType,
    PaymentAttempt,
    PaymentStatus,
    PaymentWebhookEvent,
    Purchase,
    PurchaseStatus,
)
from app.models.identity import User
from app.models.subscription import (
    Subscription,
    SubscriptionPeriod,
    SubscriptionPeriodStatus,
    SubscriptionStatus,
)


class FinancialError(ValueError):
    pass


def currency_code(value: str) -> str:
    currency = value.upper()
    if len(currency) != 3 or not currency.isalpha():
        raise FinancialError("Currency must be a three-letter ISO code")
    return currency


def commission_amount(gross_amount_minor: int, basis_points: int) -> tuple[int, int]:
    if gross_amount_minor <= 0 or not 0 <= basis_points <= 10_000:
        raise FinancialError("Invalid monetary amount or commission")
    platform_fee = gross_amount_minor * basis_points // 10_000
    return platform_fee, gross_amount_minor - platform_fee


async def ppv_commission(db: AsyncSession) -> int:
    rule = await db.scalar(
        select(CommissionRule).where(
            CommissionRule.revenue_type == "ppv", CommissionRule.active.is_(True)
        )
    )
    if rule:
        return rule.basis_points
    rule = CommissionRule(
        revenue_type="ppv", basis_points=get_settings().finance_default_commission_basis_points
    )
    db.add(rule)
    await db.flush()
    return rule.basis_points


async def _account(
    db: AsyncSession, kind: LedgerAccountKind, currency: str, owner_creator_id: UUID | None = None
) -> LedgerAccount:
    query = select(LedgerAccount).where(
        LedgerAccount.kind == kind, LedgerAccount.currency == currency
    )
    if owner_creator_id is None:
        query = query.where(LedgerAccount.owner_creator_id.is_(None))
    else:
        query = query.where(LedgerAccount.owner_creator_id == owner_creator_id)
    account = await db.scalar(query.with_for_update())
    if account:
        return account
    account = LedgerAccount(kind=kind, currency=currency, owner_creator_id=owner_creator_id)
    db.add(account)
    await db.flush()
    return account


async def post_entries(
    db: AsyncSession,
    *,
    transaction_type: LedgerTransactionType,
    currency: str,
    idempotency_key: str,
    reference: str,
    entries: list[tuple[LedgerAccount, LedgerDirection, int]],
    reversal_of_transaction_id: UUID | None = None,
    metadata: dict[str, str] | None = None,
) -> LedgerTransaction:
    existing = await db.scalar(
        select(LedgerTransaction).where(LedgerTransaction.idempotency_key == idempotency_key)
    )
    if existing:
        return existing
    debit = sum(amount for _, direction, amount in entries if direction is LedgerDirection.debit)
    credit = sum(amount for _, direction, amount in entries if direction is LedgerDirection.credit)
    if not entries or debit != credit or debit <= 0:
        raise FinancialError("Ledger transaction must balance")
    if any(account.currency != currency or amount <= 0 for account, _, amount in entries):
        raise FinancialError("Ledger entry currency or amount is invalid")
    transaction = LedgerTransaction(
        transaction_type=transaction_type,
        currency=currency,
        idempotency_key=idempotency_key,
        reference=reference,
        reversal_of_transaction_id=reversal_of_transaction_id,
        effective_at=datetime.now(UTC),
        metadata_json=metadata or {},
    )
    db.add(transaction)
    await db.flush()
    db.add_all(
        LedgerEntry(
            transaction_id=transaction.id,
            ledger_account_id=account.id,
            direction=direction,
            amount_minor=amount,
            currency=currency,
        )
        for account, direction, amount in entries
    )
    await db.flush()
    return transaction


async def initiate_purchase(
    db: AsyncSession, buyer: User, content_id: UUID, idempotency_key: str
) -> Purchase:
    if not idempotency_key or len(idempotency_key) > 128:
        raise FinancialError("A valid Idempotency-Key is required")
    content = await db.scalar(
        select(ContentItem).where(ContentItem.id == content_id).with_for_update()
    )
    if not content or content.access_policy is not AccessPolicy.ppv:
        raise FinancialError("PPV content not found")
    if (
        content.status is not ContentStatus.published
        or content.moderation_status is not ModerationStatus.approved
    ):
        raise FinancialError("PPV content is not available for purchase")
    if content.created_by_user_id == buyer.id:
        raise FinancialError("Creators cannot purchase their own content")
    if not content.price_amount_minor or not content.price_currency:
        raise FinancialError("PPV content is not priced")
    existing = await db.scalar(
        select(Purchase)
        .join(PaymentAttempt)
        .where(
            PaymentAttempt.buyer_user_id == buyer.id,
            PaymentAttempt.idempotency_key == idempotency_key,
        )
    )
    if existing:
        return existing
    prior = await db.scalar(
        select(Purchase).where(
            Purchase.buyer_user_id == buyer.id, Purchase.content_id == content.id
        )
    )
    if prior:
        return prior
    currency = currency_code(content.price_currency)
    bps = await ppv_commission(db)
    fee, creator_amount = commission_amount(content.price_amount_minor, bps)
    attempt = PaymentAttempt(
        buyer_user_id=buyer.id,
        provider=get_settings().payment_provider,
        provider_reference=f"devpay_{secrets.token_urlsafe(18)}",
        amount_minor=content.price_amount_minor,
        currency=currency,
        idempotency_key=idempotency_key,
    )
    db.add(attempt)
    await db.flush()
    purchase = Purchase(
        buyer_user_id=buyer.id,
        seller_creator_id=content.owner_creator_id,
        content_id=content.id,
        payment_attempt_id=attempt.id,
        gross_amount_minor=content.price_amount_minor,
        platform_fee_minor=fee,
        creator_amount_minor=creator_amount,
        commission_basis_points=bps,
        currency=currency,
    )
    db.add(purchase)
    await db.flush()
    return purchase


def development_webhook_payload(attempt: PaymentAttempt) -> tuple[bytes, str]:
    provider = payment_provider()
    if not hasattr(provider, "payment_succeeded_payload"):
        raise FinancialError("Development payment flow is unavailable")
    return provider.payment_succeeded_payload(attempt)


def verify_development_webhook(payload: bytes, signature: str | None) -> dict[str, str]:
    try:
        event = payment_provider().verify_webhook(payload, signature)
    except PaymentProviderError as exc:
        raise FinancialError(str(exc)) from exc
    return {
        "id": event.external_event_id,
        "type": event.event_type,
        "payment_reference": event.payment_reference,
    }


async def process_development_webhook(
    db: AsyncSession, payload: bytes, signature: str | None
) -> Purchase | None:
    event = verify_development_webhook(payload, signature)
    existing = await db.scalar(
        select(PaymentWebhookEvent).where(
            PaymentWebhookEvent.provider == "development",
            PaymentWebhookEvent.external_event_id == event["id"],
        )
    )
    if existing:
        return None
    attempt = await db.scalar(
        select(PaymentAttempt)
        .where(
            PaymentAttempt.provider == "development",
            PaymentAttempt.provider_reference == event["payment_reference"],
        )
        .with_for_update()
    )
    webhook_event = PaymentWebhookEvent(
        provider="development", external_event_id=event["id"], event_type=event["type"]
    )
    db.add(webhook_event)
    if not attempt:
        await db.flush()
        webhook_event.processed_at = datetime.now(UTC)
        return None
    webhook_event.payment_attempt_id = attempt.id
    if event["type"] != "payment.succeeded":
        attempt.status = PaymentStatus.failed
        from app.subscriptions.service import fail_payment_attempt

        await fail_payment_attempt(db, attempt)
        webhook_event.processed_at = datetime.now(UTC)
        return None
    attempt.status, attempt.completed_at = PaymentStatus.succeeded, datetime.now(UTC)
    purchase = await db.scalar(
        select(Purchase).where(Purchase.payment_attempt_id == attempt.id).with_for_update()
    )
    if purchase and purchase.status is PurchaseStatus.awaiting_payment:
        await settle_purchase(db, purchase)
    elif not purchase:
        from app.models.messaging import MessageUnlockPurchase
        from app.messaging.service import settle_message_unlock
        unlock = await db.scalar(
            select(MessageUnlockPurchase).where(
                MessageUnlockPurchase.payment_attempt_id == attempt.id
            ).with_for_update()
        )
        if unlock:
            await settle_message_unlock(db, unlock)
        else:
            from app.subscriptions.service import settle_payment_attempt
            await settle_payment_attempt(db, attempt)
    webhook_event.processed_at = datetime.now(UTC)
    return purchase


async def settle_purchase(db: AsyncSession, purchase: Purchase) -> Purchase:
    if purchase.status is PurchaseStatus.paid:
        return purchase
    clearing = await _account(db, LedgerAccountKind.platform_clearing, purchase.currency)
    revenue = await _account(db, LedgerAccountKind.platform_revenue, purchase.currency)
    pending = await _account(
        db, LedgerAccountKind.creator_pending, purchase.currency, purchase.seller_creator_id
    )
    ledger = await post_entries(
        db,
        transaction_type=LedgerTransactionType.ppv_purchase,
        currency=purchase.currency,
        idempotency_key=f"purchase:{purchase.id}",
        reference=f"ppv_purchase:{purchase.id}",
        entries=[
            (clearing, LedgerDirection.debit, purchase.gross_amount_minor),
            (revenue, LedgerDirection.credit, purchase.platform_fee_minor),
            (pending, LedgerDirection.credit, purchase.creator_amount_minor),
        ],
        metadata={"purchase_id": str(purchase.id), "content_id": str(purchase.content_id)},
    )
    entitlement = await db.scalar(
        select(ContentEntitlement).where(
            ContentEntitlement.source_type == "purchase",
            ContentEntitlement.source_reference == str(purchase.id),
        )
    )
    if not entitlement:
        entitlement = ContentEntitlement(
            subject_user_id=purchase.buyer_user_id,
            content_id=purchase.content_id,
            source_type="purchase",
            source_reference=str(purchase.id),
            valid_from=datetime.now(UTC),
        )
        db.add(entitlement)
        await db.flush()
    purchase.status = PurchaseStatus.paid
    purchase.purchased_at = datetime.now(UTC)
    purchase.ledger_transaction_id = ledger.id
    purchase.entitlement_id = entitlement.id
    await record_event(
        db,
        "purchase.settled",
        actor_user_id=purchase.buyer_user_id,
        target_type="purchase",
        target_id=str(purchase.id),
    )
    return purchase


async def reconcile_succeeded_payments(db: AsyncSession, limit: int = 100) -> int:
    """Recover payment successes whose webhook transaction stopped before settlement."""
    attempts = (
        await db.scalars(
            select(PaymentAttempt)
            .join(Purchase, Purchase.payment_attempt_id == PaymentAttempt.id)
            .where(
                PaymentAttempt.status == PaymentStatus.succeeded,
                Purchase.status == PurchaseStatus.awaiting_payment,
            )
            .order_by(PaymentAttempt.completed_at, PaymentAttempt.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    reconciled = 0
    for attempt in attempts:
        purchase = await db.scalar(
            select(Purchase).where(Purchase.payment_attempt_id == attempt.id).with_for_update()
        )
        if purchase and purchase.status is PurchaseStatus.awaiting_payment:
            await settle_purchase(db, purchase)
            reconciled += 1
    # Subscription renewals share PaymentAttempt but not Purchase.  Reconcile
    # their provider-confirmed success through the subscription settlement
    # idempotency key; this never initiates another provider charge.
    from app.models.subscription import SubscriptionPeriod, SubscriptionRenewalAttempt
    from app.subscriptions.service import settle_payment_attempt

    subscription_attempts = (
        await db.scalars(
            select(PaymentAttempt)
            .join(
                SubscriptionRenewalAttempt,
                SubscriptionRenewalAttempt.payment_attempt_id == PaymentAttempt.id,
            )
            .join(
                SubscriptionPeriod,
                SubscriptionPeriod.id == SubscriptionRenewalAttempt.subscription_period_id,
            )
            .where(
                PaymentAttempt.status == PaymentStatus.succeeded,
                SubscriptionPeriod.status == "pending",
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for attempt in subscription_attempts:
        subscription = await settle_payment_attempt(db, attempt)
        if subscription:
            reconciled += 1
    return reconciled


async def creator_balances(db: AsyncSession, creator_id: UUID, currency: str) -> dict[str, int]:
    currency = currency_code(currency)
    rows = await db.execute(
        select(
            LedgerAccount.kind,
            func.coalesce(
                func.sum(
                    case(
                        (LedgerEntry.direction == LedgerDirection.credit, LedgerEntry.amount_minor),
                        else_=-LedgerEntry.amount_minor,
                    )
                ),
                0,
            ),
        )
        .join(LedgerEntry, LedgerEntry.ledger_account_id == LedgerAccount.id)
        .where(LedgerAccount.owner_creator_id == creator_id, LedgerAccount.currency == currency)
        .group_by(LedgerAccount.kind)
    )
    values = {kind.value: int(amount) for kind, amount in rows}
    return {
        "pending_amount_minor": values.get(LedgerAccountKind.creator_pending.value, 0),
        "available_amount_minor": values.get(LedgerAccountKind.creator_available.value, 0),
    }


async def creator_financial_summary(
    db: AsyncSession, creator_id: UUID, currency: str
) -> dict[str, int]:
    currency = currency_code(currency)
    balances = await creator_balances(db, creator_id, currency)
    result = await db.execute(
        select(
            func.coalesce(func.sum(Purchase.gross_amount_minor), 0),
            func.coalesce(func.sum(Purchase.platform_fee_minor), 0),
            func.coalesce(func.sum(Purchase.creator_amount_minor), 0),
        ).where(
            Purchase.seller_creator_id == creator_id,
            Purchase.currency == currency,
            Purchase.status == PurchaseStatus.paid,
        )
    )
    gross, fees, net = result.one()
    return {
        **balances,
        "ppv_gross_amount_minor": int(gross),
        "platform_fee_amount_minor": int(fees),
        "creator_net_amount_minor": int(net),
    }


async def release_creator_earnings(
    db: AsyncSession, creator_id: UUID, currency: str
) -> LedgerTransaction | None:
    settlement_seconds = get_settings().creator_earnings_settlement_seconds
    if settlement_seconds > 0:
        cutoff = datetime.now(UTC).timestamp() - settlement_seconds
        has_unsettled_purchase = await db.scalar(
            select(Purchase.id).where(
                Purchase.seller_creator_id == creator_id,
                Purchase.currency == currency,
                Purchase.status == PurchaseStatus.paid,
                Purchase.purchased_at > datetime.fromtimestamp(cutoff, UTC),
            )
        )
        if has_unsettled_purchase:
            return None
    pending = await _account(db, LedgerAccountKind.creator_pending, currency, creator_id)
    available = await _account(db, LedgerAccountKind.creator_available, currency, creator_id)
    balance = await db.scalar(
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
        ).where(LedgerEntry.ledger_account_id == pending.id)
    )
    if not balance or balance <= 0:
        return None
    release_number = await db.scalar(
        select(func.count())
        .select_from(LedgerTransaction)
        .where(
            LedgerTransaction.transaction_type == LedgerTransactionType.earnings_release,
            LedgerTransaction.currency == currency,
            LedgerTransaction.metadata_json["creator_id"].astext == str(creator_id),
        )
    )
    release_key = f"release:{creator_id}:{currency}:{int(release_number or 0) + 1}"
    return await post_entries(
        db,
        transaction_type=LedgerTransactionType.earnings_release,
        currency=currency,
        idempotency_key=release_key,
        reference=release_key,
        entries=[
            (pending, LedgerDirection.debit, int(balance)),
            (available, LedgerDirection.credit, int(balance)),
        ],
        metadata={"creator_id": str(creator_id)},
    )


async def refund_purchase(
    db: AsyncSession, purchase: Purchase, actor: User, reason: str
) -> Purchase:
    purchase = await db.scalar(select(Purchase).where(Purchase.id == purchase.id).with_for_update())
    assert purchase
    if purchase.status is PurchaseStatus.refunded:
        return purchase
    if purchase.status is not PurchaseStatus.paid or not purchase.ledger_transaction_id:
        raise FinancialError("Only settled purchases can be refunded")
    entitlement = await db.get(ContentEntitlement, purchase.entitlement_id)
    if not entitlement:
        raise FinancialError("Purchase entitlement is missing")
    clearing = await _account(db, LedgerAccountKind.platform_clearing, purchase.currency)
    revenue = await _account(db, LedgerAccountKind.platform_revenue, purchase.currency)
    pending = await _account(
        db, LedgerAccountKind.creator_pending, purchase.currency, purchase.seller_creator_id
    )
    available = await _account(
        db, LedgerAccountKind.creator_available, purchase.currency, purchase.seller_creator_id
    )
    pending_balance = await db.scalar(
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
        ).where(LedgerEntry.ledger_account_id == pending.id)
    )
    pending_reversal = min(max(int(pending_balance or 0), 0), purchase.creator_amount_minor)
    available_reversal = purchase.creator_amount_minor - pending_reversal
    entries = [
        (clearing, LedgerDirection.credit, purchase.gross_amount_minor),
        (revenue, LedgerDirection.debit, purchase.platform_fee_minor),
    ]
    if pending_reversal:
        entries.append((pending, LedgerDirection.debit, pending_reversal))
    if available_reversal:
        entries.append((available, LedgerDirection.debit, available_reversal))
    refund = await post_entries(
        db,
        transaction_type=LedgerTransactionType.refund,
        currency=purchase.currency,
        idempotency_key=f"refund:{purchase.id}",
        reference=f"refund:{purchase.id}",
        reversal_of_transaction_id=purchase.ledger_transaction_id,
        entries=entries,
        metadata={"purchase_id": str(purchase.id), "reason": reason},
    )
    purchase.status = PurchaseStatus.refunded
    entitlement.status = EntitlementStatus.revoked
    attempt = await db.get(PaymentAttempt, purchase.payment_attempt_id)
    if attempt:
        attempt.status = PaymentStatus.refunded
    await record_event(
        db,
        "purchase.refunded",
        actor_user_id=actor.id,
        target_type="purchase",
        target_id=str(purchase.id),
        metadata={"refund_transaction_id": str(refund.id), "reason": reason},
    )
    return purchase


async def refund_subscription_period(
    db: AsyncSession, period: SubscriptionPeriod, actor: User, reason: str
) -> SubscriptionPeriod:
    """Reverse one settled subscription period and revoke only its entitlement.

    A refund is a new immutable ledger transaction.  It deliberately does not
    delete or rewrite the commercial snapshot or its original charge.
    """
    period = await db.scalar(
        select(SubscriptionPeriod).where(SubscriptionPeriod.id == period.id).with_for_update()
    )
    assert period
    if period.status is SubscriptionPeriodStatus.refunded:
        return period
    if period.status is not SubscriptionPeriodStatus.active or not period.ledger_transaction_id:
        raise FinancialError("Only settled subscription periods can be refunded")
    subscription = await db.scalar(
        select(Subscription).where(Subscription.id == period.subscription_id).with_for_update()
    )
    assert subscription
    entitlement = await db.get(ContentEntitlement, period.entitlement_id)
    if not entitlement:
        raise FinancialError("Subscription entitlement is missing")
    clearing = await _account(db, LedgerAccountKind.platform_clearing, period.currency)
    revenue = await _account(db, LedgerAccountKind.platform_revenue, period.currency)
    pending = await _account(
        db, LedgerAccountKind.creator_pending, period.currency, subscription.creator_id
    )
    available = await _account(
        db, LedgerAccountKind.creator_available, period.currency, subscription.creator_id
    )
    pending_balance = await db.scalar(
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
        ).where(LedgerEntry.ledger_account_id == pending.id)
    )
    pending_reversal = min(max(int(pending_balance or 0), 0), period.creator_amount_minor)
    available_reversal = period.creator_amount_minor - pending_reversal
    entries = [
        (clearing, LedgerDirection.credit, period.charged_amount_minor),
        (revenue, LedgerDirection.debit, period.platform_fee_minor),
    ]
    if pending_reversal:
        entries.append((pending, LedgerDirection.debit, pending_reversal))
    if available_reversal:
        entries.append((available, LedgerDirection.debit, available_reversal))
    refund = await post_entries(
        db,
        transaction_type=LedgerTransactionType.refund,
        currency=period.currency,
        idempotency_key=f"subscription-refund:{period.id}",
        reference=f"subscription_refund:{period.id}",
        reversal_of_transaction_id=period.ledger_transaction_id,
        entries=entries,
        metadata={"subscription_period_id": str(period.id), "reason": reason},
    )
    period.status = SubscriptionPeriodStatus.refunded
    entitlement.status = EntitlementStatus.revoked
    entitlement.valid_until = datetime.now(UTC)
    attempt = await db.get(PaymentAttempt, period.payment_attempt_id)
    if attempt:
        attempt.status = PaymentStatus.refunded
    # Historical periods are independent.  Only a refund of the currently
    # authoritative period ends the logical subscription and its future renewal.
    if (
        subscription.current_period_start == period.period_start
        and subscription.current_period_end == period.period_end
    ):
        subscription.status = SubscriptionStatus.expired
        subscription.auto_renew = False
        subscription.cancel_at_period_end = True
        subscription.ended_at = datetime.now(UTC)
    await record_event(
        db,
        "subscription.period_refunded",
        actor_user_id=actor.id,
        target_type="subscription_period",
        target_id=str(period.id),
        metadata={"refund_transaction_id": str(refund.id), "reason": reason},
    )
    return period
