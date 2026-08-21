"""Permanent Phase 9 anti-abuse coverage for physical-order shipping treatment."""

import pytest

from app.accounts import service as accounts
from app.creators import service as creators
from app.finance import service as finance
from app.groups import service as groups
from app.marketplace import service as marketplace
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
        public_id=f"listing-{shipping}",
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


async def allowance(db, amount: int) -> MarketplaceShippingAllowance:
    row = MarketplaceShippingAllowance(
        scope=ShippingAllowanceScope.country,
        destination_code="PT",
        currency="EUR",
        allowed_shipping_minor=amount,
    )
    db.add(row)
    await db.flush()
    return row


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
    rule = CommissionRule(revenue_type="marketplace", basis_points=2_000)
    db_session.add(rule)
    configured_allowance = await allowance(db_session, 700)
    row = await listing(db_session, creator, creator_user, shipping=3_000)

    order = await marketplace.initiate_order(
        db_session, buyer, row.id, 1, "PT", "marketplace-shipping-excess"
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
    db_session.add(CommissionRule(revenue_type="marketplace", basis_points=2_000))
    await allowance(db_session, 700)
    row = await listing(db_session, creator, creator_user, shipping=600)
    order = await marketplace.initiate_order(db_session, buyer, row.id, 1, "PT", "normal-shipping")
    assert order.shipping_pass_through_minor == 600
    assert order.shipping_excess_minor == 0
    assert order.commissionable_base_minor == 500
    assert order.group_amount_minor == 0
    assert order.creator_amount_minor == 400
    # The public domain API accepts no allowance argument. A platform config is
    # the only source, and a missing config fails closed rather than trusting a creator.
    with pytest.raises(marketplace.MarketplaceError, match="Shipping is not configured"):
        await marketplace.shipping_allowance_for(db_session, "US", "EUR")
