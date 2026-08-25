"""Ledger-derived analytics.  This module never writes transactional state."""

from collections import defaultdict
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.finance import (
    LedgerAccount,
    LedgerAccountKind,
    LedgerDirection,
    LedgerEntry,
    LedgerTransaction,
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
    return {
        "metric_definition_version": METRIC_DEFINITION_VERSION,
        "starts_at": starts_at.astimezone(UTC),
        "ends_at": ends_at.astimezone(UTC),
        "currencies": [{"currency": key, **value} for key, value in sorted(totals.items())],
    }
