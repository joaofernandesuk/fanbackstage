"""Bounded, privacy-minimised finance operations projections.

The module reads canonical payment/domain/ledger history. It never creates a
parallel financial state or accepts operator-authored settlement values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditEvent
from app.models.creator import CreatorProfile
from app.models.featuring import FeatureBooking, FeatureBookingPaymentAttempt
from app.models.finance import (
    LedgerAccount,
    LedgerEntry,
    LedgerTransaction,
    PaymentAttempt,
    PaymentRefundRequirement,
    PaymentStatus,
    PaymentWebhookEvent,
    Purchase,
    PurchasePaymentAttempt,
    RefundRequirementStatus,
)
from app.models.identity import User
from app.models.marketplace import MarketplaceOrder
from app.models.messaging import MessageUnlockPurchase, PendingMessageSend
from app.models.streaming import LiveCommerceCharge, PrivateSession, PrivateSessionSettlement
from app.models.subscription import Subscription, SubscriptionPeriod, SubscriptionRenewalAttempt


@dataclass(frozen=True)
class FinanceSource:
    domain: str
    id: UUID
    status: str
    user_id: UUID
    creator_id: UUID | None
    ledger_transaction_id: UUID | None
    label: str


def _value(value: object) -> str:
    return str(getattr(value, "value", value))


def _source_condition(domain: str):
    if domain == "ppv":
        return exists(
            select(PurchasePaymentAttempt.id).where(
                PurchasePaymentAttempt.payment_attempt_id == PaymentAttempt.id
            )
        )
    if domain == "subscription":
        return exists(
            select(SubscriptionRenewalAttempt.id).where(
                SubscriptionRenewalAttempt.payment_attempt_id == PaymentAttempt.id
            )
        )
    if domain == "marketplace":
        return exists(
            select(MarketplaceOrder.id).where(
                MarketplaceOrder.payment_attempt_id == PaymentAttempt.id
            )
        )
    if domain == "featuring":
        return exists(
            select(FeatureBookingPaymentAttempt.id).where(
                FeatureBookingPaymentAttempt.payment_attempt_id == PaymentAttempt.id
            )
        )
    if domain == "message_unlock":
        return exists(
            select(MessageUnlockPurchase.id).where(
                MessageUnlockPurchase.payment_attempt_id == PaymentAttempt.id
            )
        )
    if domain == "paid_message":
        return exists(
            select(PendingMessageSend.id).where(
                PendingMessageSend.payment_attempt_id == PaymentAttempt.id
            )
        )
    if domain == "private_live":
        return exists(
            select(PrivateSession.id).where(PrivateSession.payment_attempt_id == PaymentAttempt.id)
        )
    if domain == "live_commerce":
        return exists(
            select(LiveCommerceCharge.id).where(
                LiveCommerceCharge.payment_attempt_id == PaymentAttempt.id
            )
        )
    raise ValueError("Unknown finance source domain")


async def _sources(db: AsyncSession, attempt_ids: list[UUID]) -> dict[UUID, FinanceSource]:
    if not attempt_ids:
        return {}
    result: dict[UUID, FinanceSource] = {}
    ppv = await db.execute(
        select(PurchasePaymentAttempt.payment_attempt_id, Purchase)
        .join(Purchase, Purchase.id == PurchasePaymentAttempt.purchase_id)
        .where(PurchasePaymentAttempt.payment_attempt_id.in_(attempt_ids))
    )
    for attempt_id, row in ppv:
        result[attempt_id] = FinanceSource(
            "ppv",
            row.id,
            _value(row.status),
            row.buyer_user_id,
            row.seller_creator_id,
            row.ledger_transaction_id,
            "PPV purchase",
        )
    periods = await db.execute(
        select(SubscriptionRenewalAttempt.payment_attempt_id, SubscriptionPeriod, Subscription)
        .join(
            SubscriptionPeriod,
            SubscriptionPeriod.id == SubscriptionRenewalAttempt.subscription_period_id,
        )
        .join(Subscription, Subscription.id == SubscriptionPeriod.subscription_id)
        .where(SubscriptionRenewalAttempt.payment_attempt_id.in_(attempt_ids))
    )
    for attempt_id, row, subscription in periods:
        result[attempt_id] = FinanceSource(
            "subscription",
            row.id,
            _value(row.status),
            subscription.subscriber_user_id,
            subscription.creator_id,
            row.ledger_transaction_id,
            f"Subscription period {row.sequence}",
        )
    direct_models = (
        (
            "marketplace",
            MarketplaceOrder,
            "buyer_user_id",
            "seller_creator_id",
            "ledger_transaction_id",
            "Marketplace order",
        ),
        (
            "message_unlock",
            MessageUnlockPurchase,
            "buyer_user_id",
            "seller_creator_id",
            "ledger_transaction_id",
            "Message attachment unlock",
        ),
        (
            "paid_message",
            PendingMessageSend,
            "buyer_user_id",
            "creator_id",
            "ledger_transaction_id",
            "Paid message",
        ),
        (
            "private_live",
            PrivateSession,
            "payer_user_id",
            "creator_id",
            None,
            "Private Live session",
        ),
        (
            "live_commerce",
            LiveCommerceCharge,
            "buyer_user_id",
            "creator_id",
            "ledger_transaction_id",
            "Live commerce",
        ),
    )
    for domain, model, user_field, creator_field, ledger_field, label in direct_models:
        rows = await db.scalars(select(model).where(model.payment_attempt_id.in_(attempt_ids)))
        for row in rows:
            ledger_id = getattr(row, ledger_field) if ledger_field else None
            if domain == "private_live":
                settlement = await db.scalar(
                    select(PrivateSessionSettlement).where(
                        PrivateSessionSettlement.private_session_id == row.id
                    )
                )
                ledger_id = settlement.ledger_transaction_id if settlement else None
            result[row.payment_attempt_id] = FinanceSource(
                domain,
                row.id,
                _value(row.status),
                getattr(row, user_field),
                getattr(row, creator_field),
                ledger_id,
                label,
            )
    bookings = await db.execute(
        select(FeatureBookingPaymentAttempt.payment_attempt_id, FeatureBooking)
        .join(FeatureBooking, FeatureBooking.id == FeatureBookingPaymentAttempt.booking_id)
        .where(FeatureBookingPaymentAttempt.payment_attempt_id.in_(attempt_ids))
    )
    for attempt_id, row in bookings:
        result[attempt_id] = FinanceSource(
            "featuring",
            row.id,
            _value(row.status),
            row.purchaser_user_id,
            row.owner_creator_id,
            row.ledger_transaction_id,
            "Featuring booking",
        )
    return result


async def search_payments(
    db: AsyncSession,
    *,
    search: str | None = None,
    creator: str | None = None,
    provider: str | None = None,
    status: PaymentStatus | None = None,
    currency: str | None = None,
    source_domain: str | None = None,
    refund_state: str | None = None,
    exceptions_only: bool = False,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    page: int = 1,
    page_size: int = 25,
) -> dict:
    filters = []
    query = select(PaymentAttempt).join(User, User.id == PaymentAttempt.buyer_user_id)
    count = (
        select(func.count())
        .select_from(PaymentAttempt)
        .join(User, User.id == PaymentAttempt.buyer_user_id)
    )
    if search:
        pattern = f"%{search.strip()}%"
        filters.append(
            or_(User.email.ilike(pattern), PaymentAttempt.provider_reference.ilike(pattern))
        )
    if provider:
        filters.append(PaymentAttempt.provider == provider)
    if status:
        filters.append(PaymentAttempt.status == status)
    if currency:
        filters.append(PaymentAttempt.currency == currency.upper())
    if source_domain:
        filters.append(_source_condition(source_domain))
    if refund_state:
        requirement = exists(
            select(PaymentRefundRequirement.id).where(
                PaymentRefundRequirement.payment_attempt_id == PaymentAttempt.id,
                PaymentRefundRequirement.status == RefundRequirementStatus(refund_state),
            )
        )
        filters.append(requirement)
    if exceptions_only:
        filters.append(
            or_(
                exists(
                    select(PaymentRefundRequirement.id).where(
                        PaymentRefundRequirement.payment_attempt_id == PaymentAttempt.id,
                        PaymentRefundRequirement.status == RefundRequirementStatus.required,
                    )
                ),
                PaymentAttempt.status == PaymentStatus.disputed,
                (
                    (PaymentAttempt.status == PaymentStatus.pending)
                    & (PaymentAttempt.created_at < datetime.now(UTC) - timedelta(days=1))
                ),
            )
        )
    if starts_at:
        filters.append(PaymentAttempt.created_at >= starts_at)
    if ends_at:
        filters.append(PaymentAttempt.created_at <= ends_at)
    if creator:
        pattern = f"%{creator.strip()}%"
        creator_ids = select(CreatorProfile.id).where(
            or_(CreatorProfile.username.ilike(pattern), CreatorProfile.display_name.ilike(pattern))
        )
        filters.append(
            or_(
                exists(
                    select(Purchase.id).where(
                        Purchase.payment_attempt_id == PaymentAttempt.id,
                        Purchase.seller_creator_id.in_(creator_ids),
                    )
                ),
                exists(
                    select(PurchasePaymentAttempt.id)
                    .join(Purchase, Purchase.id == PurchasePaymentAttempt.purchase_id)
                    .where(
                        PurchasePaymentAttempt.payment_attempt_id == PaymentAttempt.id,
                        Purchase.seller_creator_id.in_(creator_ids),
                    )
                ),
                exists(
                    select(SubscriptionRenewalAttempt.id)
                    .join(
                        SubscriptionPeriod,
                        SubscriptionPeriod.id == SubscriptionRenewalAttempt.subscription_period_id,
                    )
                    .join(Subscription, Subscription.id == SubscriptionPeriod.subscription_id)
                    .where(
                        SubscriptionRenewalAttempt.payment_attempt_id == PaymentAttempt.id,
                        Subscription.creator_id.in_(creator_ids),
                    )
                ),
                exists(
                    select(MarketplaceOrder.id).where(
                        MarketplaceOrder.payment_attempt_id == PaymentAttempt.id,
                        MarketplaceOrder.seller_creator_id.in_(creator_ids),
                    )
                ),
                exists(
                    select(MessageUnlockPurchase.id).where(
                        MessageUnlockPurchase.payment_attempt_id == PaymentAttempt.id,
                        MessageUnlockPurchase.seller_creator_id.in_(creator_ids),
                    )
                ),
                exists(
                    select(PendingMessageSend.id).where(
                        PendingMessageSend.payment_attempt_id == PaymentAttempt.id,
                        PendingMessageSend.creator_id.in_(creator_ids),
                    )
                ),
                exists(
                    select(PrivateSession.id).where(
                        PrivateSession.payment_attempt_id == PaymentAttempt.id,
                        PrivateSession.creator_id.in_(creator_ids),
                    )
                ),
                exists(
                    select(LiveCommerceCharge.id).where(
                        LiveCommerceCharge.payment_attempt_id == PaymentAttempt.id,
                        LiveCommerceCharge.creator_id.in_(creator_ids),
                    )
                ),
                exists(
                    select(FeatureBookingPaymentAttempt.id)
                    .join(
                        FeatureBooking,
                        FeatureBooking.id == FeatureBookingPaymentAttempt.booking_id,
                    )
                    .where(
                        FeatureBookingPaymentAttempt.payment_attempt_id == PaymentAttempt.id,
                        FeatureBooking.owner_creator_id.in_(creator_ids),
                    )
                ),
            )
        )
    total = int(await db.scalar(count.where(*filters)) or 0)
    attempts = list(
        await db.scalars(
            query.where(*filters)
            .order_by(PaymentAttempt.created_at.desc(), PaymentAttempt.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    source_map = await _sources(db, [row.id for row in attempts])
    requirements = (
        {
            row.payment_attempt_id: row
            for row in await db.scalars(
                select(PaymentRefundRequirement).where(
                    PaymentRefundRequirement.payment_attempt_id.in_([item.id for item in attempts])
                )
            )
        }
        if attempts
        else {}
    )
    creator_ids = [source.creator_id for source in source_map.values() if source.creator_id]
    creators = (
        {
            row.id: row
            for row in await db.scalars(
                select(CreatorProfile).where(CreatorProfile.id.in_(creator_ids))
            )
        }
        if creator_ids
        else {}
    )
    users = {
        row.id: row
        for row in await db.scalars(
            select(User).where(User.id.in_([item.buyer_user_id for item in attempts]))
        )
    }
    return {
        "items": [
            {
                "id": str(attempt.id),
                "provider": attempt.provider,
                "provider_reference": attempt.provider_reference,
                "amount_minor": attempt.amount_minor,
                "currency": attempt.currency,
                "status": attempt.status.value,
                "created_at": attempt.created_at,
                "completed_at": attempt.completed_at,
                "buyer": users[attempt.buyer_user_id].email
                if attempt.buyer_user_id in users
                else "Account unavailable",
                "source": (
                    {
                        "domain": source_map[attempt.id].domain,
                        "id": str(source_map[attempt.id].id),
                        "label": source_map[attempt.id].label,
                        "status": source_map[attempt.id].status,
                    }
                    if attempt.id in source_map
                    else None
                ),
                "creator": (
                    (
                        creators[source_map[attempt.id].creator_id].display_name
                        or creators[source_map[attempt.id].creator_id].username
                    )
                    if attempt.id in source_map and source_map[attempt.id].creator_id in creators
                    else None
                ),
                "refund_requirement": (
                    {
                        "id": str(requirements[attempt.id].id),
                        "status": requirements[attempt.id].status.value,
                        "reason": requirements[attempt.id].reason,
                    }
                    if attempt.id in requirements
                    else None
                ),
            }
            for attempt in attempts
        ],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


async def payment_detail(db: AsyncSession, payment_attempt_id: UUID) -> dict | None:
    attempt = await db.get(PaymentAttempt, payment_attempt_id)
    if attempt is None:
        return None
    source = (await _sources(db, [attempt.id])).get(attempt.id)
    user = await db.get(User, attempt.buyer_user_id)
    creator = (
        await db.get(CreatorProfile, source.creator_id) if source and source.creator_id else None
    )
    ledger_ids: list[UUID] = []
    if source and source.ledger_transaction_id:
        ledger_ids.append(source.ledger_transaction_id)
        ledger_ids.extend(
            await db.scalars(
                select(LedgerTransaction.id).where(
                    LedgerTransaction.reversal_of_transaction_id == source.ledger_transaction_id
                )
            )
        )
    requirement = await db.scalar(
        select(PaymentRefundRequirement).where(
            PaymentRefundRequirement.payment_attempt_id == attempt.id
        )
    )
    if requirement:
        ledger_ids.extend(
            [requirement.liability_ledger_transaction_id]
            + (
                [requirement.refund_ledger_transaction_id]
                if requirement.refund_ledger_transaction_id
                else []
            )
        )
    transactions = (
        list(
            await db.scalars(
                select(LedgerTransaction)
                .where(LedgerTransaction.id.in_(set(ledger_ids)))
                .order_by(LedgerTransaction.created_at)
            )
        )
        if ledger_ids
        else []
    )
    entries = (
        await db.execute(
            select(LedgerEntry, LedgerAccount)
            .join(LedgerAccount, LedgerAccount.id == LedgerEntry.ledger_account_id)
            .where(LedgerEntry.transaction_id.in_([row.id for row in transactions]))
            .order_by(LedgerEntry.created_at)
        )
        if transactions
        else []
    )
    entry_map: dict[UUID, list[dict]] = {}
    for entry, account in entries:
        entry_map.setdefault(entry.transaction_id, []).append(
            {
                "direction": entry.direction.value,
                "amount_minor": entry.amount_minor,
                "currency": entry.currency,
                "account": account.kind.value,
                "creator_id": str(account.owner_creator_id) if account.owner_creator_id else None,
                "group_id": str(account.owner_group_id) if account.owner_group_id else None,
                "user_id": str(account.owner_user_id) if account.owner_user_id else None,
            }
        )
    webhooks = list(
        await db.scalars(
            select(PaymentWebhookEvent)
            .where(PaymentWebhookEvent.payment_attempt_id == attempt.id)
            .order_by(PaymentWebhookEvent.created_at)
        )
    )
    target_ids = (
        [str(attempt.id)]
        + ([str(source.id)] if source else [])
        + [str(row.id) for row in transactions]
    )
    audits = list(
        await db.scalars(
            select(AuditEvent)
            .where(AuditEvent.target_id.in_(target_ids))
            .order_by(AuditEvent.created_at.desc())
            .limit(100)
        )
    )
    return {
        "payment": {
            "id": str(attempt.id),
            "provider": attempt.provider,
            "provider_reference": attempt.provider_reference,
            "amount_minor": attempt.amount_minor,
            "currency": attempt.currency,
            "status": attempt.status.value,
            "created_at": attempt.created_at,
            "completed_at": attempt.completed_at,
        },
        "buyer": {"email": user.email if user else "Account unavailable"},
        "creator": (
            {"username": creator.username, "display_name": creator.display_name}
            if creator
            else None
        ),
        "source": (
            {
                "domain": source.domain,
                "id": str(source.id),
                "label": source.label,
                "status": source.status,
            }
            if source
            else None
        ),
        "refund_requirement": (
            {
                "id": str(requirement.id),
                "status": requirement.status.value,
                "reason": requirement.reason,
                "amount_minor": requirement.amount_minor,
                "resolved_at": requirement.resolved_at,
            }
            if requirement
            else None
        ),
        "ledger": [
            {
                "id": str(row.id),
                "type": row.transaction_type.value,
                "reference": row.reference,
                "effective_at": row.effective_at,
                "reversal_of": str(row.reversal_of_transaction_id)
                if row.reversal_of_transaction_id
                else None,
                "entries": entry_map.get(row.id, []),
            }
            for row in transactions
        ],
        "provider_events": [
            {
                "id": str(row.id),
                "external_event_id": row.external_event_id,
                "type": row.event_type,
                "processed_at": row.processed_at,
                "created_at": row.created_at,
            }
            for row in webhooks
        ],
        "audit": [
            {
                "id": str(row.id),
                "type": row.event_type,
                "actor_user_id": str(row.actor_user_id) if row.actor_user_id else None,
                "created_at": row.created_at,
            }
            for row in audits
        ],
        "can_request_refund": attempt.provider == "staging_sandbox"
        and attempt.status in {PaymentStatus.succeeded, PaymentStatus.disputed},
    }


async def exception_counts(db: AsyncSession) -> dict[str, int]:
    refund_required = int(
        await db.scalar(
            select(func.count())
            .select_from(PaymentRefundRequirement)
            .where(PaymentRefundRequirement.status == RefundRequirementStatus.required)
        )
        or 0
    )
    disputed = int(
        await db.scalar(
            select(func.count())
            .select_from(PaymentAttempt)
            .where(PaymentAttempt.status == PaymentStatus.disputed)
        )
        or 0
    )
    stale_pending = int(
        await db.scalar(
            select(func.count())
            .select_from(PaymentAttempt)
            .where(
                PaymentAttempt.status == PaymentStatus.pending,
                PaymentAttempt.created_at < datetime.now(UTC) - timedelta(days=1),
            )
        )
        or 0
    )
    total = int(
        await db.scalar(
            select(func.count())
            .select_from(PaymentAttempt)
            .where(
                or_(
                    exists(
                        select(PaymentRefundRequirement.id).where(
                            PaymentRefundRequirement.payment_attempt_id == PaymentAttempt.id,
                            PaymentRefundRequirement.status == RefundRequirementStatus.required,
                        )
                    ),
                    PaymentAttempt.status == PaymentStatus.disputed,
                    (
                        (PaymentAttempt.status == PaymentStatus.pending)
                        & (PaymentAttempt.created_at < datetime.now(UTC) - timedelta(days=1))
                    ),
                )
            )
        )
        or 0
    )
    return {
        "refund_required": refund_required,
        "open_disputes": disputed,
        "stale_pending": stale_pending,
        "total": total,
    }
