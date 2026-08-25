from datetime import UTC, datetime, timedelta

import pytest

from app.accounts import service as accounts
from app.analytics import service
from app.api.routes.analytics import _safe_cell
from app.creators import service as creators
from app.finance.service import _account, post_entries
from app.models.finance import LedgerAccountKind, LedgerDirection, LedgerTransactionType


@pytest.mark.asyncio
async def test_creator_analytics_is_ledger_derived_and_currency_separated(db_session):
    owner, _ = await accounts.register(
        db_session, "analytics-owner@example.com", "strong-password-123", None
    )
    creator = await creators.get_or_create_profile(db_session, owner)
    now = datetime.now(UTC)
    eur_pending = await _account(db_session, LedgerAccountKind.creator_pending, "EUR", creator.id)
    usd_pending = await _account(db_session, LedgerAccountKind.creator_pending, "USD", creator.id)
    clearing_eur = await _account(db_session, LedgerAccountKind.platform_clearing, "EUR")
    clearing_usd = await _account(db_session, LedgerAccountKind.platform_clearing, "USD")
    await post_entries(
        db_session,
        transaction_type=LedgerTransactionType.ppv_purchase,
        currency="EUR",
        idempotency_key="analytics-eur",
        reference="analytics-eur",
        entries=[
            (clearing_eur, LedgerDirection.debit, 1000),
            (eur_pending, LedgerDirection.credit, 1000),
        ],
    )
    await post_entries(
        db_session,
        transaction_type=LedgerTransactionType.refund,
        currency="EUR",
        idempotency_key="analytics-refund",
        reference="analytics-refund",
        entries=[
            (eur_pending, LedgerDirection.debit, 250),
            (clearing_eur, LedgerDirection.credit, 250),
        ],
    )
    await post_entries(
        db_session,
        transaction_type=LedgerTransactionType.subscription_charge,
        currency="USD",
        idempotency_key="analytics-usd",
        reference="analytics-usd",
        entries=[
            (clearing_usd, LedgerDirection.debit, 500),
            (usd_pending, LedgerDirection.credit, 500),
        ],
    )
    report = await service.creator_overview(
        db_session, creator.id, now - timedelta(days=1), now + timedelta(days=1)
    )
    by_currency = {row["currency"]: row for row in report["currencies"]}
    assert by_currency["EUR"]["gross_sales_minor"] == 1000
    assert by_currency["EUR"]["creator_net_minor"] == 750
    assert by_currency["EUR"]["reversed_minor"] == 250
    assert by_currency["USD"]["gross_sales_minor"] == 500
    assert report["metric_definition_version"] == "phase14.v1"


def test_analytics_csv_formula_cells_are_neutralized():
    for value in ("=SUM(A1:A2)", "+1", "-1", "@cmd"):
        assert _safe_cell(value) == f"'{value}"
    assert _safe_cell("EUR") == "EUR"
