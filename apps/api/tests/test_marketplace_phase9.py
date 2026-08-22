"""Permanent Phase 9 anti-abuse coverage for physical-order shipping treatment."""

import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.accounts import service as accounts
from app.api.routes import admin as admin_routes
from app.api.routes import marketplace as marketplace_routes
from app.core.config import get_settings
from app.creators import service as creators
from app.finance import service as finance
from app.groups import service as groups
from app.marketplace import service as marketplace
from app.models.audit import AuditEvent
from app.models.content import ModerationStatus
from app.models.creator import CreatorStatus
from app.models.finance import (
    CommissionRule,
    LedgerTransaction,
    LedgerTransactionType,
    PaymentAttempt,
)
from app.models.groups import GroupManagerMembership, GroupPermission, GroupPermissionGrant
from app.models.marketplace import (
    MarketplaceCondition,
    MarketplaceListing,
    MarketplaceListingStatus,
    MarketplaceOrderStatus,
    MarketplaceSellerTier,
    MarketplaceShippingAllowance,
    MarketplaceShippingMode,
    MarketplaceTrackingEvent,
    ShippingAllowanceScope,
)
from app.models.social import SocialReport
from app.schemas.marketplace import (
    MarketplaceHoldPolicyInput,
    MarketplaceSellerTierInput,
    MarketplaceShipmentInput,
    ShippingAllowanceInput,
)
from app.schemas.social import ReportInput


async def approved_creator(db, email: str):
    user, _ = await accounts.register(db, email, "strong-password-123", None)
    profile = await creators.get_or_create_profile(db, user)
    await creators.update_profile(
        db, profile, {"username": email.split("@")[0], "display_name": "Creator"}, user.id
    )
    await creators.submit(db, profile, user.id)
    await creators.development_verify(db, profile, True, user.id)
    await creators.set_status(db, profile, CreatorStatus.approved, user.id)
    return user, profile


async def listing(db, creator, user, *, shipping: int) -> MarketplaceListing:
    row = MarketplaceListing(
        public_id=f"listing-{shipping}-{secrets.token_hex(4)}",
        owner_creator_id=creator.id,
        created_by_user_id=user.id,
        title="Signed print",
        category="prints",
        condition=MarketplaceCondition.new,
        status=MarketplaceListingStatus.published,
        moderation_status=ModerationStatus.approved,
        quantity_available=10,
        price_amount_minor=500,
        currency="EUR",
        shipping_mode=MarketplaceShippingMode.worldwide,
        origin_country_code="PT",
        shipping_charged_minor=shipping,
    )
    db.add(row)
    await db.flush()
    return row


async def allowance(db, amount: int, country_code: str = "PT") -> MarketplaceShippingAllowance:
    existing = await db.scalar(
        select(MarketplaceShippingAllowance).where(
            MarketplaceShippingAllowance.scope == ShippingAllowanceScope.country,
            MarketplaceShippingAllowance.destination_code == country_code,
            MarketplaceShippingAllowance.currency == "EUR",
        )
    )
    if existing:
        existing.allowed_shipping_minor = amount
        existing.active = True
        return existing
    row = MarketplaceShippingAllowance(
        scope=ShippingAllowanceScope.country,
        destination_code=country_code,
        country_code=country_code,
        currency="EUR",
        allowed_shipping_minor=amount,
    )
    db.add(row)
    await db.flush()
    return row


async def marketplace_commission(db) -> None:
    rule = await db.scalar(
        select(CommissionRule).where(CommissionRule.revenue_type == "marketplace")
    )
    if rule:
        rule.basis_points = 2_000
    else:
        db.add(CommissionRule(revenue_type="marketplace", basis_points=2_000))


@pytest.mark.asyncio
async def test_shipping_treatment_passes_through_only_server_allowance(db_session):
    assert marketplace.shipping_treatment(500, 4, 7) == {
        "item_subtotal_minor": 500,
        "shipping_charged_minor": 4,
        "shipping_allowance_minor": 7,
        "shipping_pass_through_minor": 4,
        "shipping_excess_minor": 0,
        "commissionable_base_minor": 500,
        "total_paid_minor": 504,
    }
    exact = marketplace.shipping_treatment(500, 7, 7)
    assert exact["shipping_pass_through_minor"] == 7
    assert exact["shipping_excess_minor"] == 0
    excess = marketplace.shipping_treatment(500, 30, 7)
    assert excess["shipping_pass_through_minor"] == 7
    assert excess["shipping_excess_minor"] == 23
    assert excess["commissionable_base_minor"] == 523


@pytest.mark.asyncio
async def test_checkout_snapshots_allowance_and_applies_group_only_to_shipping_excess(db_session):
    manager, _ = await accounts.register(
        db_session, "market-manager@example.com", "strong-password-123", None
    )
    creator_user, creator = await approved_creator(db_session, "market-creator@example.com")
    buyer, _ = await accounts.register(
        db_session, "market-buyer@example.com", "strong-password-123", None
    )
    group = await groups.create_group(
        db_session, manager, "Market group", "market-group", 5_000, None
    )
    membership = await groups.invite_creator(
        db_session, group.id, manager, creator.id, 5_000, [GroupPermission.manage_marketplace]
    )
    await groups.accept_invitation(db_session, membership.id, creator_user)
    await marketplace_commission(db_session)
    configured_allowance = await allowance(db_session, 700, "AA")
    row = await listing(db_session, creator, creator_user, shipping=3_000)

    order = await marketplace.initiate_order(
        db_session, buyer, row.id, 1, "AA", "marketplace-shipping-excess"
    )
    # The creator only supplied the customer charge.  The configured allowance,
    # not any checkout payload, determines the non-commissionable component.
    assert order.item_subtotal_minor == 500
    assert order.shipping_charged_minor == 3_000
    assert order.shipping_allowance_minor == 700
    assert order.shipping_pass_through_minor == 700
    assert order.shipping_excess_minor == 2_300
    assert order.commissionable_base_minor == 2_800
    assert order.total_paid_minor == 3_500
    assert order.platform_fee_minor == 560
    assert order.creator_amount_minor == 1_120
    assert order.group_amount_minor == 1_120

    attempt = await db_session.get(PaymentAttempt, order.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    assert await finance.process_development_webhook(db_session, payload, signature) is None
    assert order.creator_amount_minor == 1_120
    assert order.group_amount_minor == 1_120
    ledger = await db_session.get(LedgerTransaction, order.ledger_transaction_id)
    assert ledger
    assert ledger.metadata_json["shipping_pass_through_minor"] == "700"
    assert ledger.metadata_json["shipping_excess_minor"] == "2300"
    assert ledger.metadata_json["commissionable_base_minor"] == "2800"
    assert ledger.metadata_json["group_amount_minor"] == "1120"
    dashboard = await groups.group_financial_dashboard(db_session, group.id, manager, "EUR")
    assert dashboard["source_amounts_minor"]["marketplace"] == 1_120
    summary = await finance.creator_financial_summary(db_session, creator.id, "EUR")
    # The creator-side figure includes the immutable pass-through component.
    assert summary["marketplace_net_amount_minor"] == 1_820

    # A later platform allowance edit cannot rewrite the paid order or ledger.
    configured = await db_session.get(MarketplaceShippingAllowance, configured_allowance.id)
    assert configured
    configured.allowed_shipping_minor = 100
    await db_session.flush()
    assert order.shipping_allowance_minor == 700
    assert ledger.metadata_json["shipping_allowance_minor"] == "700"


@pytest.mark.asyncio
async def test_normal_shipping_has_no_group_split_and_allowance_is_not_a_creator_parameter(
    db_session,
):
    creator_user, creator = await approved_creator(db_session, "normal-market-creator@example.com")
    buyer, _ = await accounts.register(
        db_session, "normal-market-buyer@example.com", "strong-password-123", None
    )
    await marketplace_commission(db_session)
    await allowance(db_session, 700, "AB")
    row = await listing(db_session, creator, creator_user, shipping=600)
    order = await marketplace.initiate_order(db_session, buyer, row.id, 1, "AB", "normal-shipping")
    assert order.shipping_pass_through_minor == 600
    assert order.shipping_excess_minor == 0
    assert order.commissionable_base_minor == 500
    assert order.group_amount_minor == 0
    assert order.creator_amount_minor == 400
    # The public domain API accepts no allowance argument. A platform config is
    # the only source, and a missing config fails closed rather than trusting a creator.
    with pytest.raises(marketplace.MarketplaceError, match="Shipping is not configured"):
        await marketplace.shipping_allowance_for(db_session, "US", "ZZZ")


@pytest.mark.asyncio
async def test_admin_allowance_precedence_authorization_and_audit(db_session):
    admin, _ = await accounts.register(
        db_session, "shipping-admin@example.com", "strong-password-123", None
    )
    await accounts.assign_role(db_session, admin, "admin", admin.id, None)
    creator, _ = await accounts.register(
        db_session, "shipping-creator@example.com", "strong-password-123", None
    )

    with pytest.raises(HTTPException) as forbidden:
        await admin_routes.configure_shipping_allowance(
            ShippingAllowanceInput(currency="EUR", allowed_shipping_minor=100),
            (creator, None),
            db_session,
        )
    assert forbidden.value.status_code == 403

    global_default = await admin_routes.configure_shipping_allowance(
        ShippingAllowanceInput(currency="EUR", allowed_shipping_minor=100),
        (admin, None),
        db_session,
    )
    country_default = await admin_routes.configure_shipping_allowance(
        ShippingAllowanceInput(country_code="AC", currency="EUR", allowed_shipping_minor=300),
        (admin, None),
        db_session,
    )
    region_override = await admin_routes.configure_shipping_allowance(
        ShippingAllowanceInput(
            country_code="AC", region_code="LIS", currency="EUR", allowed_shipping_minor=500
        ),
        (admin, None),
        db_session,
    )
    assert global_default.scope == "global"
    assert country_default.scope == "country"
    assert region_override.scope == "country_region"
    assert (
        await marketplace.shipping_allowance_for(db_session, "AC", "EUR", "LIS")
    ).id == region_override.id
    assert (
        await marketplace.shipping_allowance_for(db_session, "AC", "EUR", "POR")
    ).id == country_default.id
    assert (
        await marketplace.shipping_allowance_for(db_session, "US", "EUR")
    ).id == global_default.id

    events = (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.target_id == str(region_override.id),
                AuditEvent.actor_user_id == admin.id,
            )
        )
    ).all()
    assert len(events) == 1
    assert events[0].event_type in {
        "marketplace.shipping_allowance_created",
        "marketplace.shipping_allowance_updated",
    }
    assert events[0].actor_user_id == admin.id
    assert events[0].metadata_json["new"]["allowed_shipping_minor"] == 500


@pytest.mark.asyncio
async def test_admin_change_applies_only_to_new_checkout_snapshots(db_session):
    admin, _ = await accounts.register(
        db_session, "snapshot-admin@example.com", "strong-password-123", None
    )
    await accounts.assign_role(db_session, admin, "admin", admin.id, None)
    creator_user, creator = await approved_creator(db_session, "snapshot-creator@example.com")
    buyer, _ = await accounts.register(
        db_session, "snapshot-buyer@example.com", "strong-password-123", None
    )
    await marketplace_commission(db_session)
    await admin_routes.configure_shipping_allowance(
        ShippingAllowanceInput(country_code="AD", currency="EUR", allowed_shipping_minor=700),
        (admin, None),
        db_session,
    )
    first_listing = await listing(db_session, creator, creator_user, shipping=1_000)
    first = await marketplace.initiate_order(
        db_session, buyer, first_listing.id, 1, "AD", "allowance-snapshot-before"
    )
    first_attempt = await db_session.get(PaymentAttempt, first.payment_attempt_id)
    assert first_attempt
    payload, signature = finance.development_webhook_payload(first_attempt)
    assert await finance.process_development_webhook(db_session, payload, signature) is None
    first_ledger = await db_session.get(LedgerTransaction, first.ledger_transaction_id)
    assert first_ledger

    await admin_routes.configure_shipping_allowance(
        ShippingAllowanceInput(country_code="AD", currency="EUR", allowed_shipping_minor=100),
        (admin, None),
        db_session,
    )
    second_listing = await listing(db_session, creator, creator_user, shipping=1_000)
    second = await marketplace.initiate_order(
        db_session, buyer, second_listing.id, 1, "AD", "allowance-snapshot-after"
    )
    assert first.shipping_allowance_minor == 700
    assert first.shipping_pass_through_minor == 700
    assert first.shipping_excess_minor == 300
    assert first_ledger.metadata_json["shipping_allowance_minor"] == "700"
    assert second.shipping_allowance_minor == 100
    assert second.shipping_pass_through_minor == 100
    assert second.shipping_excess_minor == 900


@pytest.mark.asyncio
async def test_listing_ownership_delegation_and_stock_reservation(db_session):
    manager, _ = await accounts.register(
        db_session, "listing-manager@example.com", "strong-password-123", None
    )
    creator_user, creator = await approved_creator(db_session, "listing-creator@example.com")
    buyer_a, _ = await accounts.register(
        db_session, "listing-buyer-a@example.com", "strong-password-123", None
    )
    buyer_b, _ = await accounts.register(
        db_session, "listing-buyer-b@example.com", "strong-password-123", None
    )
    group = await groups.create_group(
        db_session, manager, "Listing group", "listing-group", 10_000, None
    )
    membership = await groups.invite_creator(
        db_session,
        group.id,
        manager,
        creator.id,
        10_000,
        [GroupPermission.manage_marketplace],
    )
    await groups.accept_invitation(db_session, membership.id, creator_user)
    created = await marketplace.create_listing(
        db_session,
        manager,
        creator_id=creator.id,
        title="One-off item",
        description=None,
        category="collectible",
        condition="used",
        quantity_available=1,
        price_amount_minor=500,
        currency="EUR",
        shipping_mode="worldwide",
        origin_country_code="PT",
        shipping_charged_minor=100,
        media_asset_ids=[],
    )
    assert created.owner_creator_id == creator.id
    assert created.created_by_user_id == manager.id
    await marketplace.submit_listing_for_review(db_session, creator_user, created.id, creator.id)
    # Direct service use in this focused state test simulates the authorized
    # moderation route; public availability is never client-selected.
    created.status = MarketplaceListingStatus.published
    created.moderation_status = ModerationStatus.approved
    await marketplace_commission(db_session)
    await allowance(db_session, 100, "AE")
    first = await marketplace.initiate_order(
        db_session, buyer_a, created.id, 1, "AE", "single-stock-a"
    )
    assert first.quantity == 1
    assert created.quantity_available == 0
    with pytest.raises(marketplace.MarketplaceError, match="sold out"):
        await marketplace.initiate_order(db_session, buyer_b, created.id, 1, "AE", "single-stock-b")


@pytest.mark.asyncio
async def test_marketplace_earnings_release_requires_delivery_and_hold(db_session):
    creator_user, creator = await approved_creator(db_session, "hold-creator@example.com")
    buyer, _ = await accounts.register(
        db_session, "hold-buyer@example.com", "strong-password-123", None
    )
    await marketplace_commission(db_session)
    # The hold is configuration, not a test fixture default: set the expected
    # active tier policy explicitly so prior E2E/admin changes cannot make the
    # delivery-hold invariant depend on shared database state.
    hold_policy = await marketplace.hold_policy_for_tier(
        db_session, MarketplaceSellerTier.new_seller
    )
    hold_policy.hold_duration_seconds = 3_600
    await allowance(db_session, 100, "AF")
    row = await listing(db_session, creator, creator_user, shipping=100)
    row.status = MarketplaceListingStatus.published
    row.moderation_status = ModerationStatus.approved
    order = await marketplace.initiate_order(db_session, buyer, row.id, 1, "AF", "hold-order")
    attempt = await db_session.get(PaymentAttempt, order.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    assert await finance.process_development_webhook(db_session, payload, signature) is None
    assert await marketplace.release_eligible_marketplace_earnings(db_session) == 0
    await marketplace.mark_order_processing(db_session, order.id, creator_user, creator.id)
    await marketplace.mark_order_shipped(
        db_session, order.id, creator_user, creator.id, "CTT", "TRACK-1"
    )
    await marketplace.confirm_order_delivery(db_session, order.id, buyer)
    assert order.delivered_at
    assert order.earnings_hold_until and order.earnings_hold_until > datetime.now(UTC)
    assert await marketplace.release_eligible_marketplace_earnings(db_session) == 0
    order.earnings_hold_until = datetime.now(UTC) - timedelta(seconds=1)
    assert await marketplace.release_eligible_marketplace_earnings(db_session) == 1
    assert await marketplace.release_eligible_marketplace_earnings(db_session) == 0
    balances = await finance.creator_balances(db_session, creator.id, "EUR")
    assert balances == {"pending_amount_minor": 0, "available_amount_minor": 500}


@pytest.mark.asyncio
async def test_admin_tier_and_hold_changes_are_audited_and_do_not_rewrite_order_snapshot(
    db_session,
):
    admin, _ = await accounts.register(
        db_session, "tier-admin@example.com", "strong-password-123", None
    )
    await accounts.assign_role(db_session, admin, "admin", admin.id, None)
    creator_user, creator = await approved_creator(db_session, "tier-creator@example.com")
    buyer, _ = await accounts.register(
        db_session, "tier-buyer@example.com", "strong-password-123", None
    )
    await admin_routes.change_marketplace_seller_tier(
        creator.id,
        MarketplaceSellerTierInput(tier="trusted", reason="Established fulfilment history"),
        (admin, None),
        db_session,
    )
    await admin_routes.configure_marketplace_hold_policy(
        "trusted",
        MarketplaceHoldPolicyInput(hold_duration_seconds=123, active=True),
        (admin, None),
        db_session,
    )
    await marketplace_commission(db_session)
    await allowance(db_session, 100, "AG")
    row = await listing(db_session, creator, creator_user, shipping=100)
    row.status = MarketplaceListingStatus.published
    row.moderation_status = ModerationStatus.approved
    order = await marketplace.initiate_order(db_session, buyer, row.id, 1, "AG", "tier-snapshot")
    attempt = await db_session.get(PaymentAttempt, order.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    assert await finance.process_development_webhook(db_session, payload, signature) is None
    await marketplace.mark_order_shipped(db_session, order.id, creator_user, creator.id, None, None)
    await marketplace.confirm_order_delivery(db_session, order.id, buyer)
    assert order.seller_tier_snapshot.value == "trusted"
    assert order.hold_duration_seconds_snapshot == 123

    await admin_routes.change_marketplace_seller_tier(
        creator.id,
        MarketplaceSellerTierInput(tier="high_risk", reason="Manual review"),
        (admin, None),
        db_session,
    )
    await admin_routes.configure_marketplace_hold_policy(
        "trusted",
        MarketplaceHoldPolicyInput(hold_duration_seconds=999, active=True),
        (admin, None),
        db_session,
    )
    assert order.seller_tier_snapshot.value == "trusted"
    assert order.hold_duration_seconds_snapshot == 123
    events = (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.actor_user_id == admin.id,
                AuditEvent.event_type.in_(
                    ["marketplace.seller_tier_changed", "marketplace.hold_policy_updated"]
                ),
            )
        )
    ).all()
    assert {event.event_type for event in events} == {
        "marketplace.seller_tier_changed",
        "marketplace.hold_policy_updated",
    }


@pytest.mark.asyncio
async def test_shipping_address_is_restricted_and_access_is_audited_without_address_data(
    db_session,
):
    creator_user, creator = await approved_creator(db_session, "address-creator@example.com")
    buyer, _ = await accounts.register(
        db_session, "address-buyer@example.com", "strong-password-123", None
    )
    another_buyer, _ = await accounts.register(
        db_session, "address-other-buyer@example.com", "strong-password-123", None
    )
    other_creator_user, _ = await approved_creator(db_session, "address-other-creator@example.com")
    manager, _ = await accounts.register(
        db_session, "address-manager@example.com", "strong-password-123", None
    )
    admin, _ = await accounts.register(
        db_session, "address-admin@example.com", "strong-password-123", None
    )
    await accounts.assign_role(db_session, admin, "admin", admin.id, None)
    await marketplace_commission(db_session)
    await allowance(db_session, 100, "AH")
    row = await listing(db_session, creator, creator_user, shipping=100)
    order = await marketplace.initiate_order(
        db_session,
        buyer,
        row.id,
        1,
        "AH",
        "private-address",
        shipping_address={
            "recipient_name": "Buyer Name",
            "line1": "1 Private Street",
            "line2": None,
            "city": "Lisbon",
            "region_code": None,
            "postal_code": "1000-001",
            "country_code": "AH",
        },
    )
    buyer_address = await marketplace.shipping_address_for_order(db_session, order.id, buyer)
    assert buyer_address.line1 == "1 Private Street"
    with pytest.raises(PermissionError, match="until payment succeeds"):
        await marketplace.shipping_address_for_order(db_session, order.id, creator_user)
    attempt = await db_session.get(PaymentAttempt, order.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    assert await finance.process_development_webhook(db_session, payload, signature) is None
    seller_address = await marketplace.shipping_address_for_order(
        db_session, order.id, creator_user
    )
    assert buyer_address.id == seller_address.id
    with pytest.raises(PermissionError, match="shipping address permission"):
        await marketplace.shipping_address_for_order(db_session, order.id, another_buyer)
    with pytest.raises(PermissionError, match="shipping address permission"):
        await marketplace.shipping_address_for_order(db_session, order.id, other_creator_user)
    group = await groups.create_group(
        db_session, manager, "Address group", "address-group", 10_000, None
    )
    membership = await groups.invite_creator(
        db_session, group.id, manager, creator.id, 10_000, [GroupPermission.manage_content]
    )
    await groups.accept_invitation(db_session, membership.id, creator_user)
    with pytest.raises(PermissionError, match="shipping address permission"):
        await marketplace.shipping_address_for_order(db_session, order.id, manager)
    manager_membership = await db_session.scalar(
        select(GroupManagerMembership).where(
            GroupManagerMembership.group_id == group.id,
            GroupManagerMembership.user_id == manager.id,
        )
    )
    assert manager_membership
    grant = GroupPermissionGrant(
        membership_id=membership.id,
        manager_membership_id=manager_membership.id,
        permission=GroupPermission.manage_marketplace_orders,
    )
    db_session.add(grant)
    await db_session.flush()
    assert (
        await marketplace.shipping_address_for_order(db_session, order.id, manager)
    ).id == buyer_address.id
    await db_session.delete(grant)
    await db_session.flush()
    with pytest.raises(PermissionError, match="shipping address permission"):
        await marketplace.shipping_address_for_order(db_session, order.id, manager)
    assert (
        await marketplace.shipping_address_for_order(db_session, order.id, admin)
    ).id == buyer_address.id
    audit = (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == "marketplace.shipping_address_accessed",
                AuditEvent.target_id == str(order.id),
            )
        )
    ).all()
    assert len(audit) == 4
    assert all("Private Street" not in str(event.metadata_json) for event in audit)


@pytest.mark.asyncio
async def test_verified_payment_consumes_once_and_failure_or_expiry_releases_stock(db_session):
    creator_user, creator = await approved_creator(db_session, "reservation-creator@example.com")
    buyer, _ = await accounts.register(
        db_session, "reservation-buyer@example.com", "strong-password-123", None
    )
    await marketplace_commission(db_session)
    await allowance(db_session, 100, "AI")
    paid_listing = await listing(db_session, creator, creator_user, shipping=100)
    paid_listing.quantity_available = 1
    paid_order = await marketplace.initiate_order(
        db_session, buyer, paid_listing.id, 1, "AI", "reservation-paid"
    )
    assert paid_listing.quantity_available == 0
    with pytest.raises(marketplace.MarketplaceError, match="processing"):
        await marketplace.mark_order_processing(db_session, paid_order.id, creator_user, creator.id)
    paid_attempt = await db_session.get(PaymentAttempt, paid_order.payment_attempt_id)
    assert paid_attempt
    success_payload, success_signature = finance.development_webhook_payload(paid_attempt)
    assert (
        await finance.process_development_webhook(db_session, success_payload, success_signature)
        is None
    )
    assert paid_order.status is MarketplaceOrderStatus.paid
    assert paid_listing.quantity_available == 0
    assert paid_order.ledger_transaction_id
    assert (
        await finance.process_development_webhook(db_session, success_payload, success_signature)
        is None
    )
    assert paid_listing.quantity_available == 0
    await marketplace.mark_order_processing(db_session, paid_order.id, creator_user, creator.id)
    await marketplace.mark_order_shipped(
        db_session, paid_order.id, creator_user, creator.id, "CTT", "TRACK-IMMUTABLE"
    )
    tracking = (
        await db_session.scalars(
            select(MarketplaceTrackingEvent).where(
                MarketplaceTrackingEvent.order_id == paid_order.id
            )
        )
    ).all()
    assert [(event.carrier, event.tracking_reference) for event in tracking] == [
        ("CTT", "TRACK-IMMUTABLE")
    ]
    with pytest.raises(marketplace.MarketplaceError, match="shipped"):
        await marketplace.mark_order_shipped(
            db_session, paid_order.id, creator_user, creator.id, "Other", "REWRITE"
        )

    failed_listing = await listing(db_session, creator, creator_user, shipping=100)
    failed_listing.quantity_available = 1
    failed_order = await marketplace.initiate_order(
        db_session, buyer, failed_listing.id, 1, "AI", "reservation-failed"
    )
    failed_attempt = await db_session.get(PaymentAttempt, failed_order.payment_attempt_id)
    assert failed_attempt
    failure_payload = json.dumps(
        {
            "id": f"failed-{failed_attempt.id}",
            "type": "payment.failed",
            "payment_reference": failed_attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    failure_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(), failure_payload, hashlib.sha256
    ).hexdigest()
    assert (
        await finance.process_development_webhook(db_session, failure_payload, failure_signature)
        is None
    )
    assert failed_order.status is MarketplaceOrderStatus.cancelled
    assert failed_listing.quantity_available == 1
    assert (
        await finance.process_development_webhook(db_session, failure_payload, failure_signature)
        is None
    )
    assert failed_listing.quantity_available == 1

    expired_listing = await listing(db_session, creator, creator_user, shipping=100)
    expired_listing.quantity_available = 1
    expired_order = await marketplace.initiate_order(
        db_session, buyer, expired_listing.id, 1, "AI", "reservation-expired"
    )
    expired_order.reservation_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await marketplace.expire_marketplace_reservations(db_session) == 1
    assert expired_order.status is MarketplaceOrderStatus.cancelled
    assert expired_listing.quantity_available == 1
    assert await marketplace.expire_marketplace_reservations(db_session) == 0


@pytest.mark.asyncio
async def test_unshipped_refund_reverses_original_pending_split_and_restores_stock_once(db_session):
    manager, _ = await accounts.register(
        db_session, "refund-manager@example.com", "strong-password-123", None
    )
    creator_user, creator = await approved_creator(db_session, "refund-creator@example.com")
    buyer, _ = await accounts.register(
        db_session, "refund-buyer@example.com", "strong-password-123", None
    )
    group = await groups.create_group(
        db_session, manager, "Refund group", "refund-group", 5_000, None
    )
    membership = await groups.invite_creator(db_session, group.id, manager, creator.id, 5_000, [])
    await groups.accept_invitation(db_session, membership.id, creator_user)
    await marketplace_commission(db_session)
    await allowance(db_session, 100, "AJ")
    row = await listing(db_session, creator, creator_user, shipping=100)
    row.quantity_available = 1
    order = await marketplace.initiate_order(db_session, buyer, row.id, 1, "AJ", "refund-once")
    attempt = await db_session.get(PaymentAttempt, order.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db_session, payload, signature)
    original = await db_session.get(LedgerTransaction, order.ledger_transaction_id)
    assert original and original.metadata_json["group_amount_minor"] != "0"
    assert row.quantity_available == 0

    refunded = await marketplace.cancel_order(
        db_session, order.id, creator_user, creator.id, "out of stock"
    )
    assert refunded.status is MarketplaceOrderStatus.refunded
    assert row.quantity_available == 1
    assert (await finance.creator_balances(db_session, creator.id, "EUR"))[
        "pending_amount_minor"
    ] == 0
    reversal = await db_session.scalar(
        select(LedgerTransaction).where(
            LedgerTransaction.transaction_type == LedgerTransactionType.refund,
            LedgerTransaction.reversal_of_transaction_id == original.id,
        )
    )
    assert (
        reversal
        and reversal.metadata_json["original_group_contract_id"]
        == original.metadata_json["group_contract_id"]
    )
    assert (
        await marketplace.refund_order(db_session, order.id, creator_user, "duplicate") is refunded
    )
    assert row.quantity_available == 1
    assert (
        await db_session.scalar(
            select(LedgerTransaction).where(
                LedgerTransaction.transaction_type == LedgerTransactionType.refund,
                LedgerTransaction.reversal_of_transaction_id == original.id,
            )
        )
    ).id == reversal.id


@pytest.mark.asyncio
async def test_dispute_and_chargeback_block_release_and_refund_after_release_compensates(
    db_session,
):
    creator_user, creator = await approved_creator(db_session, "dispute-creator@example.com")
    buyer, _ = await accounts.register(
        db_session, "dispute-buyer@example.com", "strong-password-123", None
    )
    admin, _ = await accounts.register(
        db_session, "dispute-admin@example.com", "strong-password-123", None
    )
    await accounts.assign_role(db_session, admin, "admin", admin.id, None)
    await marketplace_commission(db_session)
    await allowance(db_session, 100, "AK")
    row = await listing(db_session, creator, creator_user, shipping=100)
    order = await marketplace.initiate_order(db_session, buyer, row.id, 1, "AK", "dispute-release")
    attempt = await db_session.get(PaymentAttempt, order.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db_session, payload, signature)
    await marketplace.mark_order_shipped(
        db_session, order.id, creator_user, creator.id, "CTT", "DISC-1"
    )
    await marketplace.confirm_order_delivery(db_session, order.id, buyer)
    order.earnings_hold_until = datetime.now(UTC) - timedelta(seconds=1)
    await marketplace.open_order_dispute(db_session, order.id, buyer, "delivery issue")
    assert await marketplace.release_eligible_marketplace_earnings(db_session) == 0
    await marketplace.resolve_order_dispute(db_session, order.id, admin, False, "resolved")
    assert await marketplace.release_eligible_marketplace_earnings(db_session) == 1
    assert (await finance.creator_balances(db_session, creator.id, "EUR"))[
        "available_amount_minor"
    ] == 500
    original = await db_session.get(LedgerTransaction, order.ledger_transaction_id)
    assert original
    await marketplace.refund_order(db_session, order.id, admin, "post-delivery refund")
    assert order.status is MarketplaceOrderStatus.refunded
    assert (await finance.creator_balances(db_session, creator.id, "EUR"))[
        "available_amount_minor"
    ] == 0
    refund = await db_session.scalar(
        select(LedgerTransaction).where(
            LedgerTransaction.transaction_type == LedgerTransactionType.refund,
            LedgerTransaction.reversal_of_transaction_id == original.id,
        )
    )
    assert refund

    chargeback_listing = await listing(db_session, creator, creator_user, shipping=100)
    chargeback = await marketplace.initiate_order(
        db_session, buyer, chargeback_listing.id, 1, "AK", "chargeback-block"
    )
    chargeback_attempt = await db_session.get(PaymentAttempt, chargeback.payment_attempt_id)
    assert chargeback_attempt
    chargeback_payload, chargeback_signature = finance.development_webhook_payload(
        chargeback_attempt
    )
    await finance.process_development_webhook(db_session, chargeback_payload, chargeback_signature)
    await marketplace.mark_order_shipped(
        db_session, chargeback.id, creator_user, creator.id, "CTT", "CB-1"
    )
    await marketplace.confirm_order_delivery(db_session, chargeback.id, buyer)
    chargeback.earnings_hold_until = datetime.now(UTC) - timedelta(seconds=1)
    provider_chargeback = json.dumps(
        {
            "id": f"chargeback-{chargeback_attempt.id}",
            "type": "payment.chargeback",
            "payment_reference": chargeback_attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    provider_chargeback_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(), provider_chargeback, hashlib.sha256
    ).hexdigest()
    await finance.process_development_webhook(
        db_session, provider_chargeback, provider_chargeback_signature
    )
    # Replay of the same verified provider event must not make another reversal.
    await finance.process_development_webhook(
        db_session, provider_chargeback, provider_chargeback_signature
    )
    assert await marketplace.release_eligible_marketplace_earnings(db_session) == 0
    assert chargeback.status is MarketplaceOrderStatus.chargeback


@pytest.mark.asyncio
async def test_suspension_and_delegated_order_controls_are_creator_scoped_and_revocable(db_session):
    manager, _ = await accounts.register(
        db_session, "market-order-manager@example.com", "strong-password-123", None
    )
    creator_user, creator = await approved_creator(db_session, "market-order-creator@example.com")
    buyer, _ = await accounts.register(
        db_session, "market-order-buyer@example.com", "strong-password-123", None
    )
    admin, _ = await accounts.register(
        db_session, "market-suspension-admin@example.com", "strong-password-123", None
    )
    await accounts.assign_role(db_session, admin, "admin", admin.id, None)
    group = await groups.create_group(
        db_session, manager, "Orders group", "orders-group", 10_000, None
    )
    membership = await groups.invite_creator(
        db_session,
        group.id,
        manager,
        creator.id,
        10_000,
        [GroupPermission.manage_marketplace_orders],
    )
    await groups.accept_invitation(db_session, membership.id, creator_user)
    await marketplace_commission(db_session)
    await allowance(db_session, 100, "AL")
    row = await listing(db_session, creator, creator_user, shipping=100)
    order = await marketplace.initiate_order(db_session, buyer, row.id, 1, "AL", "manager-orders")
    attempt = await db_session.get(PaymentAttempt, order.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db_session, payload, signature)
    processed = await marketplace_routes.managed_order_processing(
        creator.id, order.id, (manager, None), db_session
    )
    assert processed.status == MarketplaceOrderStatus.processing.value
    shipped = await marketplace_routes.managed_order_shipped(
        creator.id,
        order.id,
        MarketplaceShipmentInput(carrier="CTT", tracking_reference="MANAGER-1"),
        (manager, None),
        db_session,
    )
    assert shipped.tracking_reference == "MANAGER-1"
    manager_membership = await db_session.scalar(
        select(GroupManagerMembership).where(
            GroupManagerMembership.group_id == group.id,
            GroupManagerMembership.user_id == manager.id,
        )
    )
    assert manager_membership
    grant = await db_session.scalar(
        select(GroupPermissionGrant).where(
            GroupPermissionGrant.membership_id == membership.id,
            GroupPermissionGrant.manager_membership_id == manager_membership.id,
            GroupPermissionGrant.permission == GroupPermission.manage_marketplace_orders,
        )
    )
    assert grant
    await db_session.delete(grant)
    await db_session.flush()
    with pytest.raises(HTTPException, match="Delegated marketplace order permission denied"):
        await marketplace_routes.managed_creator_fulfilment_orders(
            creator.id, (manager, None), db_session
        )

    await marketplace.set_marketplace_suspension(
        db_session, admin, creator.id, True, "policy review"
    )
    with pytest.raises(marketplace.MarketplaceError, match="not available"):
        await marketplace.initiate_order(db_session, buyer, row.id, 1, "AL", "suspended-checkout")
    events = (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == "marketplace.seller_suspension_changed"
            )
        )
    ).all()
    assert len(events) == 1 and events[0].actor_user_id == admin.id


@pytest.mark.asyncio
async def test_prohibited_marketplace_listing_reports_are_deduped_and_moderation_removes_listing(
    db_session,
):
    creator_user, creator = await approved_creator(db_session, "report-creator@example.com")
    buyer, _ = await accounts.register(
        db_session, "report-buyer@example.com", "strong-password-123", None
    )
    moderator, _ = await accounts.register(
        db_session, "report-moderator@example.com", "strong-password-123", None
    )
    await accounts.assign_role(db_session, moderator, "moderator", moderator.id, None)
    row = await listing(db_session, creator, creator_user, shipping=100)
    result = await marketplace_routes.report_listing(
        row.public_id,
        ReportInput(reason="prohibited_item", details="Restricted item"),
        (buyer, None),
        db_session,
    )
    assert result == {"reported": True}
    await marketplace_routes.report_listing(
        row.public_id,
        ReportInput(reason="prohibited_item", details="Repeated report"),
        (buyer, None),
        db_session,
    )
    reports = (
        await db_session.scalars(
            select(SocialReport).where(
                SocialReport.target_type == "marketplace_listing", SocialReport.target_id == row.id
            )
        )
    ).all()
    assert len(reports) == 1
    await admin_routes.remove_reported_marketplace_listing(
        reports[0].id, (moderator, None), db_session
    )
    assert row.status is MarketplaceListingStatus.removed
    audit = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == "marketplace.listing_removed_after_report",
            AuditEvent.target_id == str(row.id),
        )
    )
    assert audit and audit.actor_user_id == moderator.id
