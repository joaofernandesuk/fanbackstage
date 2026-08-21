"""Permanent Phase 9 anti-abuse coverage for physical-order shipping treatment."""

import secrets

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.accounts import service as accounts
from app.api.routes import admin as admin_routes
from app.creators import service as creators
from app.finance import service as finance
from app.groups import service as groups
from app.marketplace import service as marketplace
from app.models.audit import AuditEvent
from app.models.content import ModerationStatus
from app.models.creator import CreatorStatus
from app.models.finance import CommissionRule, LedgerTransaction, PaymentAttempt
from app.models.groups import GroupPermission
from app.models.marketplace import (
    MarketplaceCondition,
    MarketplaceListing,
    MarketplaceListingStatus,
    MarketplaceShippingAllowance,
    MarketplaceShippingMode,
    ShippingAllowanceScope,
)
from app.schemas.marketplace import ShippingAllowanceInput


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
