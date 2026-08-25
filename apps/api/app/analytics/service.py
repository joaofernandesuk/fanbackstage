"""Ledger-derived analytics.  This module never writes transactional state."""

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import ContentItem
from app.models.finance import (
    LedgerAccount,
    LedgerAccountKind,
    LedgerDirection,
    LedgerEntry,
    LedgerTransaction,
    Purchase,
    PurchaseStatus,
)
from app.models.groups import (
    GroupCreatorMembership,
    GroupManagerMembership,
    GroupMembershipStatus,
    GroupPermission,
    GroupPermissionGrant,
)
from app.models.social import Follow
from app.models.subscription import (
    Subscription,
    SubscriptionPeriod,
    SubscriptionPeriodStatus,
    SubscriptionStatus,
)

METRIC_DEFINITION_VERSION = "phase14.v1"
METRIC_DEFINITIONS = {
    "creator_net": "Net creator-side ledger movement: credits to creator pending/available less reversals.",
    "pending_earnings": "Current credit-balance projection of the creator_pending ledger account.",
    "available_earnings": "Current credit-balance projection of the creator_available ledger account.",
    "gross_sales": "Positive creator-side settlement credits before later reversal transactions.",
}


def _source(transaction_type: str) -> str:
    return {
        "ppv_purchase": "ppv",
        "subscription_charge": "subscriptions",
        "messaging_charge": "messaging",
        "private_live_session": "private_live",
        "marketplace_order": "marketplace",
    }.get(transaction_type, transaction_type)


def _signed(entry: LedgerEntry) -> int:
    return entry.amount_minor if entry.direction is LedgerDirection.credit else -entry.amount_minor


async def creator_overview(
    db: AsyncSession,
    creator_id: UUID,
    starts_at: datetime,
    ends_at: datetime,
    currency: str | None = None,
) -> dict:
    """Return currency-separated, event-time ledger totals for one creator."""
    query = (
        select(LedgerEntry, LedgerAccount, LedgerTransaction)
        .join(LedgerAccount, LedgerAccount.id == LedgerEntry.ledger_account_id)
        .join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.transaction_id)
        .where(
            LedgerAccount.owner_creator_id == creator_id,
            LedgerAccount.kind.in_(
                [LedgerAccountKind.creator_pending, LedgerAccountKind.creator_available]
            ),
            LedgerTransaction.effective_at >= starts_at,
            LedgerTransaction.effective_at < ends_at,
        )
        .order_by(LedgerTransaction.effective_at, LedgerEntry.id)
    )
    if currency:
        query = query.where(LedgerTransaction.currency == currency.upper())
    rows = (await db.execute(query)).all()
    totals: dict[str, dict] = defaultdict(
        lambda: {
            "gross_sales_minor": 0,
            "creator_net_minor": 0,
            "pending_earnings_minor": 0,
            "available_earnings_minor": 0,
            "reversed_minor": 0,
        }
    )
    sources: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"gross_sales_minor": 0, "creator_net_minor": 0, "reversed_minor": 0}
    )
    for entry, account, transaction in rows:
        bucket = totals[transaction.currency]
        value = _signed(entry)
        bucket["creator_net_minor"] += value
        if account.kind is LedgerAccountKind.creator_pending:
            bucket["pending_earnings_minor"] += value
        else:
            bucket["available_earnings_minor"] += value
        source = _source(transaction.transaction_type.value)
        source_bucket = sources[(transaction.currency, source)]
        source_bucket["creator_net_minor"] += value
        if value > 0 and transaction.transaction_type.value != "earnings_release":
            bucket["gross_sales_minor"] += value
            source_bucket["gross_sales_minor"] += value
        if value < 0:
            bucket["reversed_minor"] += -value
            source_bucket["reversed_minor"] += -value
    return {
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "metric_definitions": METRIC_DEFINITIONS,
        "starts_at": starts_at.astimezone(UTC),
        "ends_at": ends_at.astimezone(UTC),
        "currencies": [{"currency": key, **value} for key, value in sorted(totals.items())],
        "revenue_sources": [
            {"currency": currency_code, "source": source, **value}
            for (currency_code, source), value in sorted(sources.items())
        ],
    }


async def creator_audience(
    db: AsyncSession, creator_id: UUID, starts_at: datetime, ends_at: datetime
) -> dict:
    """Aggregate-only audience metrics; no follower identities are projected."""
    followers_total = await db.scalar(
        select(func.count()).select_from(Follow).where(Follow.creator_id == creator_id)
    )
    follows_created = await db.scalar(
        select(func.count())
        .select_from(Follow)
        .where(
            Follow.creator_id == creator_id,
            Follow.created_at >= starts_at,
            Follow.created_at < ends_at,
        )
    )
    active_subscribers = await db.scalar(
        select(func.count())
        .select_from(Subscription)
        .where(
            Subscription.creator_id == creator_id, Subscription.status == SubscriptionStatus.active
        )
    )
    return {
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "followers_total": int(followers_total or 0),
        "follows_created": int(follows_created or 0),
        "active_subscribers": int(active_subscribers or 0),
    }


async def creator_subscription_metrics(
    db: AsyncSession, creator_id: UUID, starts_at: datetime, ends_at: datetime
) -> dict:
    """Subscription lifecycle aggregates; subscriber identities stay outside analytics."""
    periods = (
        await db.scalars(
            select(SubscriptionPeriod)
            .join(Subscription, Subscription.id == SubscriptionPeriod.subscription_id)
            .where(
                Subscription.creator_id == creator_id,
                SubscriptionPeriod.created_at >= starts_at,
                SubscriptionPeriod.created_at < ends_at,
            )
        )
    ).all()
    subscriptions = (
        await db.scalars(
            select(Subscription).where(
                Subscription.creator_id == creator_id,
                Subscription.created_at >= starts_at,
                Subscription.created_at < ends_at,
            )
        )
    ).all()
    duration_mix: dict[str, int] = defaultdict(int)
    for period in periods:
        duration_mix[period.duration.value] += 1
    return {
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "new_subscriptions": len(subscriptions),
        "renewals": sum(
            1
            for item in periods
            if item.sequence > 1 and item.status is SubscriptionPeriodStatus.active
        ),
        "failed_renewals": sum(
            1
            for item in periods
            if item.sequence > 1 and item.status is SubscriptionPeriodStatus.failed
        ),
        "expirations": sum(
            1 for item in subscriptions if item.status is SubscriptionStatus.expired
        ),
        "payment_failed": sum(
            1 for item in subscriptions if item.status is SubscriptionStatus.payment_failed
        ),
        "cancellations": sum(1 for item in subscriptions if item.cancel_at_period_end),
        "duration_mix": dict(sorted(duration_mix.items())),
    }


async def creator_ppv_metrics(
    db: AsyncSession, creator_id: UUID, starts_at: datetime, ends_at: datetime
) -> dict:
    """Purchase aggregates retain original gross while status supplies refund visibility."""
    purchases = (
        await db.scalars(
            select(Purchase).where(
                Purchase.seller_creator_id == creator_id,
                Purchase.created_at >= starts_at,
                Purchase.created_at < ends_at,
                Purchase.status.in_(
                    [PurchaseStatus.paid, PurchaseStatus.refunded, PurchaseStatus.chargeback]
                ),
            )
        )
    ).all()
    totals: dict[str, dict] = defaultdict(
        lambda: {
            "purchases": 0,
            "unique_buyers": set(),
            "gross_minor": 0,
            "refunded_minor": 0,
            "charged_back_minor": 0,
        }
    )
    for purchase in purchases:
        bucket = totals[purchase.currency]
        bucket["purchases"] += 1
        bucket["unique_buyers"].add(purchase.buyer_user_id)
        bucket["gross_minor"] += purchase.gross_amount_minor
        if purchase.status is PurchaseStatus.refunded:
            bucket["refunded_minor"] += purchase.gross_amount_minor
        if purchase.status is PurchaseStatus.chargeback:
            bucket["charged_back_minor"] += purchase.gross_amount_minor
    content_titles = {
        item.id: item.title
        for item in (
            await db.scalars(
                select(ContentItem).where(
                    ContentItem.owner_creator_id == creator_id,
                    ContentItem.id.in_([item.content_id for item in purchases]),
                )
            )
        ).all()
    }
    content_totals: dict[tuple[str, UUID], dict] = defaultdict(
        lambda: {"purchases": 0, "gross_minor": 0}
    )
    for purchase in purchases:
        bucket = content_totals[(purchase.currency, purchase.content_id)]
        bucket["purchases"] += 1
        bucket["gross_minor"] += purchase.gross_amount_minor
    top_content = [
        {
            "content_id": str(content_id),
            "title": content_titles[content_id],
            "currency": currency,
            **value,
        }
        for (currency, content_id), value in content_totals.items()
        if content_id in content_titles
    ]
    top_content.sort(
        key=lambda item: (-item["gross_minor"], -item["purchases"], item["content_id"])
    )
    return {
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "currencies": [
            {
                "currency": code,
                "purchases": data["purchases"],
                "unique_buyers": len(data["unique_buyers"]),
                "gross_minor": data["gross_minor"],
                "refunded_minor": data["refunded_minor"],
                "charged_back_minor": data["charged_back_minor"],
                "net_minor": data["gross_minor"]
                - data["refunded_minor"]
                - data["charged_back_minor"],
            }
            for code, data in sorted(totals.items())
        ],
        "top_content": top_content[:20],
    }


async def group_overview(
    db: AsyncSession,
    group_id: UUID,
    starts_at: datetime,
    ends_at: datetime,
    currency: str | None = None,
) -> dict:
    """Event-time group earnings from immutable group accounts, never current contracts."""
    query = (
        select(LedgerEntry, LedgerAccount, LedgerTransaction)
        .join(LedgerAccount, LedgerAccount.id == LedgerEntry.ledger_account_id)
        .join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.transaction_id)
        .where(
            LedgerAccount.owner_group_id == group_id,
            LedgerAccount.kind.in_(
                [LedgerAccountKind.group_pending, LedgerAccountKind.group_available]
            ),
            LedgerTransaction.effective_at >= starts_at,
            LedgerTransaction.effective_at < ends_at,
        )
    )
    if currency:
        query = query.where(LedgerTransaction.currency == currency.upper())
    totals: dict[str, dict] = defaultdict(
        lambda: {
            "group_net_minor": 0,
            "pending_minor": 0,
            "available_minor": 0,
            "reversed_minor": 0,
        }
    )
    sources: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"group_net_minor": 0, "reversed_minor": 0}
    )
    for entry, account, transaction in (await db.execute(query)).all():
        value, bucket = _signed(entry), totals[transaction.currency]
        bucket["group_net_minor"] += value
        bucket[
            "pending_minor"
            if account.kind is LedgerAccountKind.group_pending
            else "available_minor"
        ] += value
        if value < 0:
            bucket["reversed_minor"] += -value
        source = sources[(transaction.currency, _source(transaction.transaction_type.value))]
        source["group_net_minor"] += value
        if value < 0:
            source["reversed_minor"] += -value
    return {
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "starts_at": starts_at.astimezone(UTC),
        "ends_at": ends_at.astimezone(UTC),
        "currencies": [{"currency": key, **value} for key, value in sorted(totals.items())],
        "revenue_sources": [
            {"currency": code, "source": source, **value}
            for (code, source), value in sorted(sources.items())
        ],
    }


async def current_managed_creators(db: AsyncSession, group_id: UUID, actor_id: UUID) -> list[UUID]:
    """Only active creator grants are current/private analytics scope."""
    return list(
        await db.scalars(
            select(GroupCreatorMembership.creator_id)
            .join(
                GroupPermissionGrant,
                GroupPermissionGrant.membership_id == GroupCreatorMembership.id,
            )
            .join(
                GroupManagerMembership,
                GroupManagerMembership.id == GroupPermissionGrant.manager_membership_id,
            )
            .where(
                GroupCreatorMembership.group_id == group_id,
                GroupCreatorMembership.status == GroupMembershipStatus.active,
                GroupManagerMembership.user_id == actor_id,
                GroupPermissionGrant.permission == GroupPermission.view_analytics,
            )
        )
    )


async def group_creator_comparison(
    db: AsyncSession, group_id: UUID, actor_id: UUID, starts_at: datetime, ends_at: datetime
) -> list[dict]:
    creator_ids = await current_managed_creators(db, group_id, actor_id)
    if not creator_ids:
        return []
    rows = (
        await db.execute(
            select(LedgerEntry, LedgerTransaction)
            .join(LedgerAccount, LedgerAccount.id == LedgerEntry.ledger_account_id)
            .join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.transaction_id)
            .where(
                LedgerAccount.owner_group_id == group_id,
                LedgerTransaction.effective_at >= starts_at,
                LedgerTransaction.effective_at < ends_at,
                LedgerTransaction.metadata_json["creator_id"].astext.in_(
                    [str(item) for item in creator_ids]
                ),
            )
        )
    ).all()
    totals: dict[tuple[str, str], int] = defaultdict(int)
    for entry, transaction in rows:
        creator_id = transaction.metadata_json["creator_id"]
        totals[(creator_id, transaction.currency)] += _signed(entry)
    return [
        {"creator_id": creator_id, "currency": currency, "group_net_minor": amount}
        for (creator_id, currency), amount in sorted(totals.items())
    ]
