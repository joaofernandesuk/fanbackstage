from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.accounts import service as accounts
from app.analytics import service
from app.api.routes.analytics import _csv_response, _safe_cell
from app.creators import service as creators
from app.finance.service import _account, post_entries
from app.groups import service as groups
from app.models.discovery import DiscoveryEntityType, DiscoveryEvent
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


@pytest.mark.asyncio
async def test_analytics_export_has_a_hard_50000_row_limit_before_it_writes():
    with pytest.raises(HTTPException, match="50,000") as exc:
        await _csv_response(
            None,  # type: ignore[arg-type]
            identity=(None, None),  # type: ignore[arg-type]
            scope="creator",
            filename="report.csv",
            fields=["currency"],
            rows=[{"currency": "EUR"}] * 50_001,
        )
    assert exc.value.status_code == 413


@pytest.mark.asyncio
async def test_attribution_dimensions_coexist_without_reclassifying_sponsored_as_organic(
    db_session,
):
    user, _ = await accounts.register(
        db_session, "analytics-attribution@example.com", "strong-password-123", None
    )
    now = datetime.now(UTC)
    db_session.add_all(
        [
            DiscoveryEvent(
                event_type="click",
                request_key="organic-analytics",
                actor_user_id=user.id,
                entity_type=DiscoveryEntityType.creator,
                entity_id=uuid4(),
                ranking_version=1,
                metadata_json={},
            ),
            DiscoveryEvent(
                event_type="sponsored_click",
                request_key="sponsored-analytics",
                actor_user_id=user.id,
                entity_type=DiscoveryEntityType.creator,
                entity_id=uuid4(),
                ranking_version=1,
                metadata_json={"sponsored": True, "booking_id": str(uuid4())},
            ),
        ]
    )
    await db_session.flush()
    report = await service.platform_growth_and_attribution(
        db_session, now - timedelta(days=1), now + timedelta(days=1)
    )
    dimensions = report["attribution_dimensions"]
    assert dimensions["organic_discovery_interactions"] == 1
    assert dimensions["sponsored_featuring_interactions"] == 1


@pytest.mark.asyncio
async def test_platform_and_creator_analytics_reconcile_immutable_reversal_history(db_session):
    owner, _ = await accounts.register(
        db_session, "analytics-reconcile@example.com", "strong-password-123", None
    )
    creator = await creators.get_or_create_profile(db_session, owner)
    group = await groups.create_group(
        db_session, owner, "Analytics group", "analytics-group", 5000, None
    )
    now = datetime.now(UTC)
    clearing = await _account(db_session, LedgerAccountKind.platform_clearing, "EUR")
    platform = await _account(db_session, LedgerAccountKind.platform_revenue, "EUR")
    creator_pending = await _account(
        db_session, LedgerAccountKind.creator_pending, "EUR", creator.id
    )
    group_pending = await _account(
        db_session, LedgerAccountKind.group_pending, "EUR", owner_group_id=group.id
    )
    purchase = await post_entries(
        db_session,
        transaction_type=LedgerTransactionType.ppv_purchase,
        currency="EUR",
        idempotency_key="analytics-reconcile-purchase",
        reference="analytics-reconcile-purchase",
        entries=[
            (clearing, LedgerDirection.debit, 1000),
            (platform, LedgerDirection.credit, 200),
            (creator_pending, LedgerDirection.credit, 400),
            (group_pending, LedgerDirection.credit, 400),
        ],
    )
    await post_entries(
        db_session,
        transaction_type=LedgerTransactionType.refund,
        currency="EUR",
        idempotency_key="analytics-reconcile-refund",
        reference="analytics-reconcile-refund",
        reversal_of_transaction_id=purchase.id,
        entries=[
            (creator_pending, LedgerDirection.debit, 400),
            (group_pending, LedgerDirection.debit, 400),
            (platform, LedgerDirection.debit, 200),
            (clearing, LedgerDirection.credit, 1000),
        ],
    )
    report = await service.platform_overview(
        db_session, now - timedelta(days=1), now + timedelta(days=1)
    )
    eur = {row["currency"]: row for row in report["currencies"]}["EUR"]
    assert eur["gmv_minor"] == 1000
    assert eur["refunds_minor"] == 1000
    assert eur["creator_distributable_minor"] == 0
    assert eur["group_distributable_minor"] == 0
    assert eur["platform_fee_minor"] == 0
    assert eur["platform_retained_net_minor"] == 0
