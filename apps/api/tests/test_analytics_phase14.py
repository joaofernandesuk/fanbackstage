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
from app.models.streaming import (
    LiveAccessMode,
    LiveChatKind,
    LiveChatMessage,
    LiveParticipant,
    LiveParticipantRole,
    LiveReactionAggregate,
    LiveReactionType,
    LiveRoom,
    LiveRoomStatus,
)


@pytest.mark.asyncio
async def test_creator_analytics_is_ledger_derived_and_currency_separated(db_session):
    owner, _ = await accounts.register(
        db_session, "analytics-owner@example.com", "strong-password-123", None
    )
    creator = await creators.get_or_create_profile(db_session, owner)
    now = datetime.now(UTC)
    eur_pending = await _account(db_session, LedgerAccountKind.creator_pending, "EUR", creator.id)
    eur_available = await _account(
        db_session, LedgerAccountKind.creator_available, "EUR", creator.id
    )
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
    await post_entries(
        db_session,
        transaction_type=LedgerTransactionType.payment_dispute_hold,
        currency="EUR",
        idempotency_key="analytics-dispute-hold",
        reference="analytics-dispute-hold",
        entries=[
            (eur_available, LedgerDirection.debit, 400),
            (eur_pending, LedgerDirection.credit, 400),
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
    assert all(row["source"] != "payment_dispute_hold" for row in report["revenue_sources"])


@pytest.mark.asyncio
async def test_creator_live_analytics_uses_durable_room_participant_chat_and_reaction_state(
    db_session,
):
    owner, _ = await accounts.register(
        db_session, "live-analytics-owner@example.com", "strong-password-123", None
    )
    viewer, _ = await accounts.register(
        db_session, "live-analytics-viewer@example.com", "strong-password-123", None
    )
    creator = await creators.get_or_create_profile(db_session, owner)
    now = datetime.now(UTC)
    room = LiveRoom(
        creator_id=creator.id,
        public_id="live-analytics-room",
        provider_room_name="live-analytics-provider",
        status=LiveRoomStatus.ended,
        access_mode=LiveAccessMode.public,
        title="Analytics Live",
        viewer_count=0,
        peak_viewer_count=7,
        started_at=now - timedelta(hours=1),
        ended_at=now,
    )
    db_session.add(room)
    await db_session.flush()
    db_session.add_all(
        [
            LiveParticipant(
                live_room_id=room.id,
                user_id=viewer.id,
                role=LiveParticipantRole.viewer,
                joined_at=now - timedelta(minutes=50),
                left_at=now - timedelta(minutes=5),
            ),
            LiveChatMessage(
                live_room_id=room.id,
                sender_user_id=viewer.id,
                kind=LiveChatKind.text,
                body="hello",
            ),
            LiveReactionAggregate(
                live_room_id=room.id,
                reaction_type=LiveReactionType.love,
                reaction_count=9,
            ),
        ]
    )
    await db_session.flush()

    report = await service.creator_live_metrics(
        db_session, creator.id, now - timedelta(days=1), now + timedelta(days=1)
    )

    assert report["sessions"] == 1
    assert report["live_seconds"] == 3600
    assert report["peak_viewers"] == 7
    assert report["unique_viewers"] == 1
    assert report["chat_messages"] == 1
    assert report["reactions"] == 9


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
async def test_platform_cohort_query_has_an_explicit_creator_ledger_join(db_session):
    now = datetime.now(UTC)

    report = await service.platform_cohorts_retention_and_churn(
        db_session, now - timedelta(days=1), now + timedelta(days=1)
    )

    assert report["metric_definition_version"] == "phase14.v1"
    assert "creator_activity" in report["retention"]


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
    creator_available = await _account(
        db_session, LedgerAccountKind.creator_available, "EUR", creator.id
    )
    group_available = await _account(
        db_session, LedgerAccountKind.group_available, "EUR", owner_group_id=group.id
    )
    refund_clearing = await _account(db_session, LedgerAccountKind.refund_clearing, "EUR")
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
        transaction_type=LedgerTransactionType.earnings_release,
        currency="EUR",
        idempotency_key="analytics-reconcile-release",
        reference="analytics-reconcile-release",
        entries=[
            (creator_pending, LedgerDirection.debit, 400),
            (creator_available, LedgerDirection.credit, 400),
            (group_pending, LedgerDirection.debit, 400),
            (group_available, LedgerDirection.credit, 400),
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
            (creator_available, LedgerDirection.debit, 400),
            (group_available, LedgerDirection.debit, 400),
            (platform, LedgerDirection.debit, 200),
            (clearing, LedgerDirection.credit, 1000),
        ],
    )
    liability = await post_entries(
        db_session,
        transaction_type=LedgerTransactionType.excess_capture_liability,
        currency="EUR",
        idempotency_key="analytics-excess-capture",
        reference="analytics-excess-capture",
        entries=[
            (clearing, LedgerDirection.debit, 300),
            (refund_clearing, LedgerDirection.credit, 300),
        ],
    )
    await post_entries(
        db_session,
        transaction_type=LedgerTransactionType.refund,
        currency="EUR",
        idempotency_key="analytics-excess-capture-resolution",
        reference="analytics-excess-capture-resolution",
        reversal_of_transaction_id=liability.id,
        entries=[
            (refund_clearing, LedgerDirection.debit, 300),
            (clearing, LedgerDirection.credit, 300),
        ],
        metadata={"payment_refund_requirement_id": str(uuid4())},
    )
    chargeback_liability = await post_entries(
        db_session,
        transaction_type=LedgerTransactionType.excess_capture_liability,
        currency="EUR",
        idempotency_key="analytics-excess-capture-chargeback",
        reference="analytics-excess-capture-chargeback",
        entries=[
            (clearing, LedgerDirection.debit, 200),
            (refund_clearing, LedgerDirection.credit, 200),
        ],
    )
    await post_entries(
        db_session,
        transaction_type=LedgerTransactionType.chargeback,
        currency="EUR",
        idempotency_key="analytics-excess-capture-chargeback-resolution",
        reference="analytics-excess-capture-chargeback-resolution",
        reversal_of_transaction_id=chargeback_liability.id,
        entries=[
            (refund_clearing, LedgerDirection.debit, 200),
            (clearing, LedgerDirection.credit, 200),
        ],
        metadata={"payment_refund_requirement_id": str(uuid4())},
    )
    report = await service.platform_overview(
        db_session, now - timedelta(days=1), now + timedelta(days=1)
    )
    eur = {row["currency"]: row for row in report["currencies"]}["EUR"]
    assert eur["gmv_minor"] == 1000
    assert eur["refunds_minor"] == 1000
    assert eur["chargebacks_minor"] == 0
    assert eur["creator_distributable_minor"] == 0
    assert eur["group_distributable_minor"] == 0
    assert eur["platform_fee_minor"] == 0
    assert eur["platform_retained_net_minor"] == 0
    creator_report = await service.creator_overview(
        db_session, creator.id, now - timedelta(days=1), now + timedelta(days=1)
    )
    creator_eur = {row["currency"]: row for row in creator_report["currencies"]}["EUR"]
    assert creator_eur["gross_sales_minor"] == 400
    assert creator_eur["creator_net_minor"] == 0
    assert creator_eur["reversed_minor"] == 400
    assert all(row["source"] != "earnings_release" for row in creator_report["revenue_sources"])
    group_report = await service.group_overview(
        db_session, group.id, now - timedelta(days=1), now + timedelta(days=1)
    )
    group_eur = {row["currency"]: row for row in group_report["currencies"]}["EUR"]
    assert group_eur["group_net_minor"] == 0
    assert group_eur["reversed_minor"] == 400
    assert all(row["source"] != "earnings_release" for row in group_report["revenue_sources"])
