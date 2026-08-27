"""Ledger-derived analytics.  This module never writes transactional state."""

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import ContentItem
from app.models.creator import CreatorProfile, CreatorStatus, CreatorStatusHistory
from app.models.discovery import DiscoveryEvent
from app.models.featuring import FeatureBooking, FeatureBookingStatus, FeatureRefund
from app.models.finance import (
    LedgerAccount,
    LedgerAccountKind,
    LedgerDirection,
    LedgerEntry,
    LedgerTransaction,
    LedgerTransactionType,
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
from app.models.identity import User
from app.models.marketplace import MarketplaceOrder, MarketplaceOrderStatus
from app.models.messaging import MessageUnlockPurchase, PendingMessageSend
from app.models.referral import ReferralCommissionAllocation, SignupAttribution
from app.models.social import FeedPost, Follow, PostComment, PostReaction
from app.models.streaming import (
    PrivateSession,
    PrivateSessionMode,
    PrivateSessionSettlement,
    PrivateSessionStatus,
)
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
        if transaction.transaction_type in {
            LedgerTransactionType.earnings_release,
            LedgerTransactionType.payment_dispute_hold,
        }:
            # Internal account reclassifications only move an already counted
            # allocation between pending and available. They are neither a
            # sale nor a reversal and must not create synthetic revenue
            # sources.
            continue
        source = _source(transaction.transaction_type.value)
        source_bucket = sources[(transaction.currency, source)]
        source_bucket["creator_net_minor"] += value
        if value > 0:
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


async def creator_marketplace_metrics(
    db: AsyncSession, creator_id: UUID, starts_at: datetime, ends_at: datetime
) -> dict:
    """Seller aggregates from immutable order snapshots; no address/tracking/buyer projection."""
    orders = (
        await db.scalars(
            select(MarketplaceOrder).where(
                MarketplaceOrder.seller_creator_id == creator_id,
                MarketplaceOrder.created_at >= starts_at,
                MarketplaceOrder.created_at < ends_at,
            )
        )
    ).all()
    totals: dict[str, dict] = defaultdict(
        lambda: {
            "orders": 0,
            "units": 0,
            "gmv_minor": 0,
            "creator_net_minor": 0,
            "shipping_charged_minor": 0,
            "shipping_pass_through_minor": 0,
            "shipping_excess_minor": 0,
            "platform_fee_minor": 0,
            "refunded_minor": 0,
            "charged_back_minor": 0,
            "pending_orders": 0,
            "held_orders": 0,
            "available_orders": 0,
            "reversed_orders": 0,
            "fulfilment_seconds_total": 0,
            "fulfilment_count": 0,
            "delivery_seconds_total": 0,
            "delivery_count": 0,
        }
    )
    for order in orders:
        if order.status is MarketplaceOrderStatus.awaiting_payment:
            continue
        bucket = totals[order.currency]
        bucket["orders"] += 1
        bucket["units"] += order.quantity
        bucket["gmv_minor"] += order.total_paid_minor
        bucket["creator_net_minor"] += order.creator_amount_minor
        bucket["shipping_charged_minor"] += order.shipping_charged_minor
        bucket["shipping_pass_through_minor"] += order.shipping_pass_through_minor
        bucket["shipping_excess_minor"] += order.shipping_excess_minor
        bucket["platform_fee_minor"] += order.platform_fee_minor
        if order.status is MarketplaceOrderStatus.refunded:
            bucket["refunded_minor"] += order.total_paid_minor
        if order.status is MarketplaceOrderStatus.chargeback:
            bucket["charged_back_minor"] += order.total_paid_minor
        if order.status in {MarketplaceOrderStatus.paid, MarketplaceOrderStatus.processing}:
            bucket["pending_orders"] += 1
        if order.earnings_release_status.value == "blocked":
            bucket["held_orders"] += 1
        if order.earnings_release_status.value == "released":
            bucket["available_orders"] += 1
        if order.status in {MarketplaceOrderStatus.refunded, MarketplaceOrderStatus.chargeback}:
            bucket["reversed_orders"] += 1
        if order.paid_at and order.shipped_at:
            bucket["fulfilment_seconds_total"] += int(
                (order.shipped_at - order.paid_at).total_seconds()
            )
            bucket["fulfilment_count"] += 1
        if order.shipped_at and order.delivered_at:
            bucket["delivery_seconds_total"] += int(
                (order.delivered_at - order.shipped_at).total_seconds()
            )
            bucket["delivery_count"] += 1
    return {
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "currencies": [
            {
                "currency": code,
                **value,
                "net_after_reversals_minor": value["creator_net_minor"]
                - value["refunded_minor"]
                - value["charged_back_minor"],
                "average_fulfilment_seconds": value["fulfilment_seconds_total"]
                // value["fulfilment_count"]
                if value["fulfilment_count"]
                else None,
                "average_delivery_seconds": value["delivery_seconds_total"]
                // value["delivery_count"]
                if value["delivery_count"]
                else None,
            }
            for code, value in sorted(totals.items())
        ],
    }


async def creator_content_performance(
    db: AsyncSession, creator_id: UUID, starts_at: datetime, ends_at: datetime
) -> dict:
    """Aggregate content performance without projecting viewer or buyer identities."""
    content = (
        await db.scalars(
            select(ContentItem).where(
                ContentItem.owner_creator_id == creator_id,
                ContentItem.created_at < ends_at,
            )
        )
    ).all()
    content_ids = {item.id for item in content}
    purchases = (
        (
            await db.scalars(
                select(Purchase).where(
                    Purchase.seller_creator_id == creator_id,
                    Purchase.content_id.in_(content_ids),
                    Purchase.created_at >= starts_at,
                    Purchase.created_at < ends_at,
                    Purchase.status.in_(
                        [PurchaseStatus.paid, PurchaseStatus.refunded, PurchaseStatus.chargeback]
                    ),
                )
            )
        ).all()
        if content_ids
        else []
    )
    posts = (
        (
            await db.scalars(
                select(FeedPost).where(
                    FeedPost.creator_id == creator_id, FeedPost.source_content_id.in_(content_ids)
                )
            )
        ).all()
        if content_ids
        else []
    )
    post_ids = {item.id for item in posts}
    reactions = (
        (
            await db.scalars(
                select(PostReaction).where(
                    PostReaction.post_id.in_(post_ids),
                    PostReaction.created_at >= starts_at,
                    PostReaction.created_at < ends_at,
                )
            )
        ).all()
        if post_ids
        else []
    )
    comments = (
        (
            await db.scalars(
                select(PostComment).where(
                    PostComment.post_id.in_(post_ids),
                    PostComment.created_at >= starts_at,
                    PostComment.created_at < ends_at,
                    PostComment.hidden_at.is_(None),
                    PostComment.deleted_at.is_(None),
                )
            )
        ).all()
        if post_ids
        else []
    )
    post_content = {item.id: item.source_content_id for item in posts}
    totals: dict[UUID, dict] = defaultdict(
        lambda: {
            "impressions": 0,
            "views": 0,
            "engagement": 0,
            "ppv_purchases": 0,
            "gross_minor": 0,
            "net_minor": 0,
        }
    )
    for item in reactions:
        totals[post_content[item.post_id]]["engagement"] += 1
    for item in comments:
        totals[post_content[item.post_id]]["engagement"] += 1
    for item in purchases:
        bucket = totals[item.content_id]
        bucket["ppv_purchases"] += 1
        bucket["gross_minor"] += item.gross_amount_minor
        bucket["net_minor"] += (
            item.creator_amount_minor if item.status is PurchaseStatus.paid else 0
        )
    events = (
        (
            await db.scalars(
                select(DiscoveryEvent).where(
                    DiscoveryEvent.entity_id.in_(content_ids),
                    DiscoveryEvent.created_at >= starts_at,
                    DiscoveryEvent.created_at < ends_at,
                )
            )
        ).all()
        if content_ids
        else []
    )
    for item in events:
        bucket = totals[item.entity_id]
        if item.event_type == "impression":
            bucket["impressions"] += 1
        elif item.event_type == "view":
            bucket["views"] += 1
    title_by_id = {item.id: item.title for item in content}
    rows = [
        {
            "content_id": str(key),
            "title": title_by_id[key],
            **value,
            "ppv_conversion": value["ppv_purchases"] / value["views"] if value["views"] else None,
        }
        for key, value in totals.items()
    ]
    return {
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "top_by_revenue": sorted(rows, key=lambda item: (-item["gross_minor"], item["content_id"]))[
            :20
        ],
        "top_by_engagement": sorted(
            rows, key=lambda item: (-item["engagement"], item["content_id"])
        )[:20],
    }


async def creator_messaging_metrics(
    db: AsyncSession, creator_id: UUID, starts_at: datetime, ends_at: datetime
) -> dict:
    """Commercial message aggregates only; message bodies and recipient identities never project."""
    unlocks = (
        await db.scalars(
            select(MessageUnlockPurchase).where(
                MessageUnlockPurchase.seller_creator_id == creator_id,
                MessageUnlockPurchase.created_at >= starts_at,
                MessageUnlockPurchase.created_at < ends_at,
            )
        )
    ).all()
    sends = (
        await db.scalars(
            select(PendingMessageSend).where(
                PendingMessageSend.creator_id == creator_id,
                PendingMessageSend.created_at >= starts_at,
                PendingMessageSend.created_at < ends_at,
            )
        )
    ).all()
    totals: dict[str, dict] = defaultdict(
        lambda: {
            "paid_sends": 0,
            "paid_unlocks": 0,
            "unique_payers": set(),
            "gross_minor": 0,
            "creator_net_minor": 0,
        }
    )
    for row in unlocks:
        if row.status not in {"paid", "refunded", "chargeback"}:
            continue
        bucket = totals[row.currency]
        bucket["paid_unlocks"] += 1
        bucket["unique_payers"].add(row.buyer_user_id)
        bucket["gross_minor"] += row.gross_amount_minor
        bucket["creator_net_minor"] += row.creator_amount_minor
    for row in sends:
        if row.status not in {"paid", "refunded", "chargeback"}:
            continue
        bucket = totals[row.currency]
        bucket["paid_sends"] += 1
        bucket["unique_payers"].add(row.buyer_user_id)
        bucket["gross_minor"] += row.gross_amount_minor
        bucket["creator_net_minor"] += row.creator_amount_minor
    return {
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "currencies": [
            {
                "currency": code,
                **{key: value for key, value in data.items() if key != "unique_payers"},
                "unique_paying_users": len(data["unique_payers"]),
            }
            for code, data in sorted(totals.items())
        ],
    }


async def creator_private_live_metrics(
    db: AsyncSession, creator_id: UUID, starts_at: datetime, ends_at: datetime
) -> dict:
    sessions = (
        await db.scalars(
            select(PrivateSession).where(
                PrivateSession.creator_id == creator_id,
                PrivateSession.created_at >= starts_at,
                PrivateSession.created_at < ends_at,
            )
        )
    ).all()
    settlement_by_session = {
        item.private_session_id: item
        for item in (
            await db.scalars(
                select(PrivateSessionSettlement)
                .join(PrivateSession)
                .where(
                    PrivateSession.creator_id == creator_id,
                    PrivateSession.created_at >= starts_at,
                    PrivateSession.created_at < ends_at,
                )
            )
        ).all()
    }
    totals: dict[str, dict] = defaultdict(
        lambda: {
            "session_count": 0,
            "billable_seconds": 0,
            "gross_minor": 0,
            "creator_net_minor": 0,
            "one_to_one": 0,
            "two_to_one": 0,
            "reconnecting": 0,
            "failed": 0,
        }
    )
    for session in sessions:
        bucket = totals[session.currency]
        bucket["session_count"] += 1
        bucket["billable_seconds"] += session.billable_seconds
        bucket["one_to_one" if session.mode is PrivateSessionMode.one_to_one else "two_to_one"] += 1
        if session.status is PrivateSessionStatus.reconnecting:
            bucket["reconnecting"] += 1
        if session.status is PrivateSessionStatus.failed:
            bucket["failed"] += 1
        if settlement := settlement_by_session.get(session.id):
            bucket["gross_minor"] += settlement.gross_amount_minor
            bucket["creator_net_minor"] += settlement.creator_amount_minor
    return {
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "currencies": [
            {
                "currency": code,
                **data,
                "average_duration_seconds": data["billable_seconds"] // data["session_count"]
                if data["session_count"]
                else 0,
            }
            for code, data in sorted(totals.items())
        ],
    }


async def creator_referral_metrics(
    db: AsyncSession, creator_id: UUID, starts_at: datetime, ends_at: datetime
) -> dict:
    rows = (
        await db.scalars(
            select(ReferralCommissionAllocation).where(
                ReferralCommissionAllocation.beneficiary_creator_id == creator_id,
                ReferralCommissionAllocation.allocated_at >= starts_at,
                ReferralCommissionAllocation.allocated_at < ends_at,
            )
        )
    ).all()
    from app.referrals.service import (
        active_marketplace_dispute_hold_source_ids,
        allocation_projection_status,
    )

    active_hold_source_ids = await active_marketplace_dispute_hold_source_ids(
        db, [row.source_ledger_transaction_id for row in rows]
    )
    totals: dict[str, dict] = defaultdict(
        lambda: {
            "referral_earnings_minor": 0,
            "pending_minor": 0,
            "available_minor": 0,
            "reversed_minor": 0,
            "attributed_volume_minor": 0,
        }
    )
    for item in rows:
        bucket = totals[item.currency]
        bucket["referral_earnings_minor"] += item.amount_minor
        bucket["attributed_volume_minor"] += item.platform_fee_minor
        projection_status = allocation_projection_status(item, active_hold_source_ids)
        if projection_status == "reversed":
            bucket["reversed_minor"] += item.amount_minor
        elif projection_status == "available":
            bucket["available_minor"] += item.amount_minor
        else:
            bucket["pending_minor"] += item.amount_minor
    return {
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "currencies": [{"currency": code, **data} for code, data in sorted(totals.items())],
    }


async def creator_featuring_metrics(
    db: AsyncSession, creator_id: UUID, starts_at: datetime, ends_at: datetime
) -> dict:
    bookings = (
        await db.scalars(
            select(FeatureBooking).where(
                FeatureBooking.owner_creator_id == creator_id,
                FeatureBooking.created_at >= starts_at,
                FeatureBooking.created_at < ends_at,
            )
        )
    ).all()
    refunds = {item.booking_id: item for item in (await db.scalars(select(FeatureRefund))).all()}
    events = (
        await db.scalars(
            select(DiscoveryEvent).where(
                DiscoveryEvent.created_at >= starts_at,
                DiscoveryEvent.created_at < ends_at,
            )
        )
    ).all()
    totals: dict[str, dict] = defaultdict(
        lambda: {
            "spend_minor": 0,
            "bookings": 0,
            "refunds_minor": 0,
            "impressions": 0,
            "clicks": 0,
            "conversions": 0,
        }
    )
    booking_targets = {item.target_id for item in bookings}
    for item in bookings:
        bucket = totals[item.currency]
        if item.status is not FeatureBookingStatus.awaiting_payment:
            bucket["bookings"] += 1
            bucket["spend_minor"] += item.price_minor
        if refund := refunds.get(item.id):
            bucket["refunds_minor"] += refund.amount_minor
    for event in events:
        if (
            event.entity_id not in booking_targets
            or not event.metadata_json.get("sponsored")
            or not event.event_type.startswith("sponsored_")
        ):
            continue
        # Event names are deliberately generic so event instrumentation can evolve independently.
        for data in totals.values():
            if event.event_type == "sponsored_impression":
                data["impressions"] += 1
            elif event.event_type == "sponsored_click":
                data["clicks"] += 1
            elif event.event_type == "sponsored_conversion":
                data["conversions"] += 1
    return {
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "currencies": [
            {
                "currency": code,
                **data,
                "ctr": data["clicks"] / data["impressions"] if data["impressions"] else None,
                "cpc_minor": data["spend_minor"] // data["clicks"] if data["clicks"] else None,
                "conversion_cost_minor": data["spend_minor"] // data["conversions"]
                if data["conversions"]
                else None,
            }
            for code, data in sorted(totals.items())
        ],
    }


async def platform_overview(db: AsyncSession, starts_at: datetime, ends_at: datetime) -> dict:
    """Platform BI is ledger/event-time derived and keeps every currency isolated."""
    entries = (
        await db.execute(
            select(LedgerEntry, LedgerAccount, LedgerTransaction)
            .join(LedgerAccount, LedgerAccount.id == LedgerEntry.ledger_account_id)
            .join(LedgerTransaction, LedgerTransaction.id == LedgerEntry.transaction_id)
            .where(
                LedgerTransaction.effective_at >= starts_at,
                LedgerTransaction.effective_at < ends_at,
            )
        )
    ).all()
    totals: dict[str, dict] = defaultdict(
        lambda: {
            "gmv_minor": 0,
            "platform_fee_minor": 0,
            "platform_retained_net_minor": 0,
            "creator_distributable_minor": 0,
            "group_distributable_minor": 0,
            "refunds_minor": 0,
            "chargebacks_minor": 0,
            "featuring_revenue_minor": 0,
        }
    )
    seen_gross: set[UUID] = set()
    for entry, account, transaction in entries:
        data = totals[transaction.currency]
        if transaction.id not in seen_gross and transaction.transaction_type.value not in {
            "refund",
            "chargeback",
            "earnings_release",
            "excess_capture_liability",
            "payment_dispute_hold",
        }:
            gross = sum(
                item.amount_minor
                for item, acct, _ in entries
                if _.id == transaction.id
                and acct.kind is LedgerAccountKind.platform_clearing
                and item.direction is LedgerDirection.debit
            )
            data["gmv_minor"] += gross
            seen_gross.add(transaction.id)
        if account.kind is LedgerAccountKind.platform_revenue:
            data["platform_fee_minor"] += _signed(entry)
            data["platform_retained_net_minor"] += _signed(entry)
            if transaction.transaction_type.value == "featuring_charge":
                data["featuring_revenue_minor"] += _signed(entry)
        elif account.kind in {
            LedgerAccountKind.creator_pending,
            LedgerAccountKind.creator_available,
        }:
            data["creator_distributable_minor"] += _signed(entry)
        elif account.kind in {LedgerAccountKind.group_pending, LedgerAccountKind.group_available}:
            data["group_distributable_minor"] += _signed(entry)
        if transaction.transaction_type.value == "refund" and not transaction.metadata_json.get(
            "payment_refund_requirement_id"
        ):
            data["refunds_minor"] += (
                entry.amount_minor
                if entry.direction is LedgerDirection.credit
                and account.kind is LedgerAccountKind.platform_clearing
                else 0
            )
        if transaction.transaction_type.value == "chargeback" and not transaction.metadata_json.get(
            "payment_refund_requirement_id"
        ):
            data["chargebacks_minor"] += (
                entry.amount_minor
                if entry.direction is LedgerDirection.credit
                and account.kind is LedgerAccountKind.platform_clearing
                else 0
            )
    allocations = (
        await db.scalars(
            select(ReferralCommissionAllocation).where(
                ReferralCommissionAllocation.allocated_at >= starts_at,
                ReferralCommissionAllocation.allocated_at < ends_at,
            )
        )
    ).all()
    for item in allocations:
        data = totals[item.currency]
        data["referral_affiliate_commission_minor"] = (
            data.get("referral_affiliate_commission_minor", 0) + item.amount_minor
        )
        data["platform_retained_net_minor"] -= item.amount_minor
    users = await db.scalar(select(func.count()).select_from(User))
    paid_users = await db.scalar(
        select(func.count(func.distinct(Purchase.buyer_user_id))).where(
            Purchase.created_at >= starts_at,
            Purchase.created_at < ends_at,
            Purchase.status.in_(
                [PurchaseStatus.paid, PurchaseStatus.refunded, PurchaseStatus.chargeback]
            ),
        )
    )
    new_creators = await db.scalar(
        select(func.count())
        .select_from(CreatorProfile)
        .where(CreatorProfile.created_at >= starts_at, CreatorProfile.created_at < ends_at)
    )
    active_creators = await db.scalar(
        select(func.count(func.distinct(LedgerAccount.owner_creator_id)))
        .join(LedgerEntry)
        .join(LedgerTransaction)
        .where(
            LedgerAccount.owner_creator_id.is_not(None),
            LedgerAccount.kind.in_(
                [LedgerAccountKind.creator_pending, LedgerAccountKind.creator_available]
            ),
            LedgerTransaction.effective_at >= starts_at,
            LedgerTransaction.effective_at < ends_at,
        )
    )
    approved_creators = await db.scalar(
        select(func.count())
        .select_from(CreatorProfile)
        .where(CreatorProfile.status == CreatorStatus.approved)
    )
    return {
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "users": int(users or 0),
        "paid_users": int(paid_users or 0),
        "new_creators": int(new_creators or 0),
        "active_creators": int(active_creators or 0),
        "approved_creators": int(approved_creators or 0),
        "currencies": [{"currency": code, **value} for code, value in sorted(totals.items())],
    }


async def platform_growth_and_attribution(
    db: AsyncSession, starts_at: datetime, ends_at: datetime
) -> dict:
    """Versioned aggregate funnel/cohort inputs; dimensions coexist and never write source domains."""
    new_users = await db.scalar(
        select(func.count())
        .select_from(User)
        .where(User.created_at >= starts_at, User.created_at < ends_at)
    )
    activated_users = await db.scalar(
        select(func.count(func.distinct(DiscoveryEvent.actor_user_id))).where(
            DiscoveryEvent.actor_user_id.is_not(None),
            DiscoveryEvent.created_at >= starts_at,
            DiscoveryEvent.created_at < ends_at,
        )
    )
    all_purchases = (
        await db.scalars(
            select(Purchase).where(
                Purchase.status.in_(
                    [PurchaseStatus.paid, PurchaseStatus.refunded, PurchaseStatus.chargeback]
                )
            )
        )
    ).all()
    purchases = [item for item in all_purchases if starts_at <= item.created_at < ends_at]
    first_purchase_at: dict[UUID, datetime] = {}
    for item in all_purchases:
        first_purchase_at[item.buyer_user_id] = min(
            first_purchase_at.get(item.buyer_user_id, item.created_at), item.created_at
        )
    first_purchase = sum(
        starts_at <= item.created_at < ends_at
        and first_purchase_at[item.buyer_user_id] == item.created_at
        for item in purchases
    )
    payer_counts: dict[UUID, int] = defaultdict(int)
    for item in purchases:
        payer_counts[item.buyer_user_id] += 1
    published = (
        await db.scalars(select(ContentItem).where(ContentItem.published_at.is_not(None)))
    ).all()
    first_content_at: dict[UUID, datetime] = {}
    for item in published:
        assert item.published_at is not None
        first_content_at[item.owner_creator_id] = min(
            first_content_at.get(item.owner_creator_id, item.published_at), item.published_at
        )
    creator_first_content = sum(
        starts_at <= item.published_at < ends_at
        and first_content_at[item.owner_creator_id] == item.published_at
        for item in published
        if item.published_at
    )
    attributions = (
        await db.scalars(
            select(SignupAttribution).where(
                SignupAttribution.attributed_at >= starts_at,
                SignupAttribution.attributed_at < ends_at,
            )
        )
    ).all()
    interactions = (
        await db.scalars(
            select(DiscoveryEvent).where(
                DiscoveryEvent.created_at >= starts_at, DiscoveryEvent.created_at < ends_at
            )
        )
    ).all()
    financial_allocations = await db.scalar(
        select(func.count())
        .select_from(ReferralCommissionAllocation)
        .where(
            ReferralCommissionAllocation.allocated_at >= starts_at,
            ReferralCommissionAllocation.allocated_at < ends_at,
        )
    )
    new_creators = await db.scalar(
        select(func.count())
        .select_from(CreatorProfile)
        .where(CreatorProfile.created_at >= starts_at, CreatorProfile.created_at < ends_at)
    )
    approvals = await db.scalar(
        select(func.count())
        .select_from(CreatorProfile)
        .where(CreatorProfile.status == CreatorStatus.approved, CreatorProfile.created_at < ends_at)
    )
    return {
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "definitions": {
            "active_user": "A user with a recorded first-party discovery interaction in the range.",
            "repeat_payer": "A payer with two or more settled purchase records in the range.",
            "creator_activity": "A creator with a ledger-derived creator-side earning in the range.",
        },
        "user_funnel": {
            "signup": int(new_users or 0),
            "activated_user": int(activated_users or 0),
            "first_purchase": first_purchase,
            "repeat_purchase": sum(value >= 2 for value in payer_counts.values()),
        },
        "creator_funnel": {
            "creator_signup": int(new_creators or 0),
            "approval": int(approvals or 0),
            "first_published_content": creator_first_content,
            "first_creator_revenue": len({item.seller_creator_id for item in purchases}),
        },
        "attribution_dimensions": {
            "referral_acquisition": len(attributions),
            "organic_discovery_interactions": sum(
                not bool(item.metadata_json.get("sponsored"))
                and not item.event_type.startswith("sponsored_")
                for item in interactions
            ),
            "sponsored_featuring_interactions": sum(
                bool(item.metadata_json.get("sponsored"))
                or item.event_type.startswith("sponsored_")
                for item in interactions
            ),
            "financial_allocations": int(financial_allocations or 0),
        },
    }


def _week_key(occurred_at: datetime) -> str:
    year, week, _ = occurred_at.isocalendar()
    return f"{year}-W{week:02d}"


async def platform_cohorts_retention_and_churn(
    db: AsyncSession, starts_at: datetime, ends_at: datetime
) -> dict:
    """Bounded, aggregate cohort/retention projections from immutable event timestamps."""
    window = ends_at - starts_at
    previous_start = starts_at - window
    users = (await db.scalars(select(User))).all()
    purchases = (
        await db.scalars(
            select(Purchase).where(
                Purchase.status.in_(
                    [PurchaseStatus.paid, PurchaseStatus.refunded, PurchaseStatus.chargeback]
                )
            )
        )
    ).all()
    subscriptions = (await db.scalars(select(Subscription))).all()
    approvals = (
        await db.scalars(
            select(CreatorStatusHistory).where(
                CreatorStatusHistory.new_status == CreatorStatus.approved
            )
        )
    ).all()
    signup_week: dict[str, int] = defaultdict(int)
    signup_month: dict[str, int] = defaultdict(int)
    for item in users:
        signup_week[_week_key(item.created_at)] += 1
        signup_month[item.created_at.strftime("%Y-%m")] += 1
    first_purchase: dict[UUID, datetime] = {}
    for item in purchases:
        first_purchase[item.buyer_user_id] = min(
            first_purchase.get(item.buyer_user_id, item.created_at), item.created_at
        )
    first_subscription: dict[UUID, datetime] = {}
    for item in subscriptions:
        first_subscription[item.subscriber_user_id] = min(
            first_subscription.get(item.subscriber_user_id, item.created_at), item.created_at
        )
    returner_events = (
        await db.scalars(
            select(DiscoveryEvent).where(
                DiscoveryEvent.actor_user_id.is_not(None),
                DiscoveryEvent.created_at >= previous_start,
                DiscoveryEvent.created_at < ends_at,
            )
        )
    ).all()
    previous_active_users = {
        item.actor_user_id
        for item in returner_events
        if previous_start <= item.created_at < starts_at
    }
    current_active_users = {
        item.actor_user_id for item in returner_events if starts_at <= item.created_at < ends_at
    }
    previous_payers = {
        item.buyer_user_id for item in purchases if previous_start <= item.created_at < starts_at
    }
    current_payers = {
        item.buyer_user_id for item in purchases if starts_at <= item.created_at < ends_at
    }
    previous_subscribers = {
        item.subscriber_user_id
        for item in subscriptions
        if item.status is SubscriptionStatus.active
        and item.current_period_start
        and previous_start <= item.current_period_start < starts_at
    }
    current_subscribers = {
        item.subscriber_user_id
        for item in subscriptions
        if item.status is SubscriptionStatus.active
        and item.current_period_start
        and starts_at <= item.current_period_start < ends_at
    }
    creator_entries = (
        await db.execute(
            select(LedgerAccount.owner_creator_id, LedgerTransaction.effective_at)
            .join(LedgerEntry)
            .join(LedgerTransaction)
            .where(
                LedgerAccount.owner_creator_id.is_not(None),
                LedgerAccount.kind.in_(
                    [LedgerAccountKind.creator_pending, LedgerAccountKind.creator_available]
                ),
                LedgerTransaction.effective_at >= previous_start,
                LedgerTransaction.effective_at < ends_at,
            )
        )
    ).all()
    previous_creators = {
        creator_id
        for creator_id, occurred_at in creator_entries
        if previous_start <= occurred_at < starts_at
    }
    current_creators = {
        creator_id
        for creator_id, occurred_at in creator_entries
        if starts_at <= occurred_at < ends_at
    }

    def retention(population: set[UUID | None], returned: set[UUID | None]) -> dict:
        denominator = len(population)
        return {
            "cohort_population": denominator,
            "returned": len(population & returned),
            "denominator": denominator,
            "rate": len(population & returned) / denominator if denominator else None,
        }

    return {
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "window": {
            "starts_at": starts_at,
            "ends_at": ends_at,
            "comparison_starts_at": previous_start,
        },
        "cohorts": {
            "signup_week": dict(sorted(signup_week.items())),
            "signup_month": dict(sorted(signup_month.items())),
            "first_purchase": dict(
                sorted(
                    (
                        _week_key(value),
                        sum(
                            _week_key(item) == _week_key(value) for item in first_purchase.values()
                        ),
                    )
                    for value in set(first_purchase.values())
                )
            ),
            "first_subscription": dict(
                sorted(
                    (
                        _week_key(value),
                        sum(
                            _week_key(item) == _week_key(value)
                            for item in first_subscription.values()
                        ),
                    )
                    for value in set(first_subscription.values())
                )
            ),
            "creator_approval": dict(
                sorted(
                    (
                        _week_key(item.created_at),
                        sum(
                            _week_key(row.created_at) == _week_key(item.created_at)
                            for row in approvals
                        ),
                    )
                    for item in approvals
                )
            ),
        },
        "retention": {
            "user_activity": {
                **retention(previous_active_users, current_active_users),
                "qualifying_activity": "first-party discovery interaction",
            },
            "payer": {
                **retention(previous_payers, current_payers),
                "qualifying_activity": "settled purchase",
            },
            "subscriber": {
                **retention(previous_subscribers, current_subscribers),
                "qualifying_activity": "active subscription",
            },
            "creator_activity": {
                **retention(previous_creators, current_creators),
                "qualifying_activity": "creator-side ledger earning",
            },
        },
        "churn": {
            "subscriber_churn": {
                "definition": "Previously active subscribers absent from current active-subscriber set",
                "count": len(previous_subscribers - current_subscribers),
                "denominator": len(previous_subscribers),
            },
            "payer_inactivity": {
                "definition": "Previous-window payers with no current-window settled purchase",
                "count": len(previous_payers - current_payers),
                "denominator": len(previous_payers),
            },
            "creator_inactivity": {
                "definition": "Previous-window active creators without current-window creator-side earning",
                "count": len(previous_creators - current_creators),
                "denominator": len(previous_creators),
            },
        },
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
        if transaction.transaction_type in {
            LedgerTransactionType.earnings_release,
            LedgerTransactionType.payment_dispute_hold,
        }:
            # Preserve pending/available balance movement without counting an
            # internal reclassification as revenue or a reversal.
            continue
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
