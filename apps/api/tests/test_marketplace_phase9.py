"""Permanent Phase 9 anti-abuse coverage for physical-order shipping treatment."""

import asyncio
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta

import pytest
from conftest import trusted_self_attested_accounts as accounts
from fastapi import HTTPException
from sqlalchemy import func, select

from app.accounts import adult_access
from app.analytics import service as analytics
from app.api.routes import admin as admin_routes
from app.api.routes import marketplace as marketplace_routes
from app.content.access import can_access_preview
from app.core.config import Settings, get_settings
from app.creators import service as creators
from app.db.session import SessionLocal
from app.finance import service as finance
from app.groups import service as groups
from app.marketplace import service as marketplace
from app.models.audit import AuditEvent
from app.models.content import (
    AccessPolicy,
    ContentItem,
    ContentStatus,
    ContentType,
    DerivativeType,
    Gallery,
    GalleryItem,
    MediaAsset,
    MediaAudience,
    MediaDerivative,
    MediaStatus,
    MediaType,
    ModerationStatus,
)
from app.models.creator import CreatorStatus
from app.models.finance import (
    CommissionRule,
    ExcessCaptureSource,
    LedgerAccount,
    LedgerAccountKind,
    LedgerDirection,
    LedgerEntry,
    LedgerTransaction,
    LedgerTransactionType,
    PaymentAttempt,
    PaymentRefundRequirement,
    PaymentStatus,
)
from app.models.groups import GroupManagerMembership, GroupPermission, GroupPermissionGrant
from app.models.identity import User
from app.models.marketplace import (
    MarketplaceCondition,
    MarketplaceEarningsReleaseStatus,
    MarketplaceListing,
    MarketplaceListingMedia,
    MarketplaceListingStatus,
    MarketplaceOrderStatus,
    MarketplaceSellerTier,
    MarketplaceShippingAllowance,
    MarketplaceShippingMode,
    MarketplaceTrackingEvent,
    ShippingAllowanceScope,
)
from app.models.messaging import UserBlock
from app.models.referral import ReferralActorType, ReferralProgramType
from app.models.social import SocialReport
from app.referrals import service as referrals
from app.schemas.marketplace import (
    MarketplaceCheckoutInput,
    MarketplaceHoldPolicyInput,
    MarketplaceSellerTierInput,
    MarketplaceShipmentInput,
    MarketplaceShippingAddressInput,
    ShippingAllowanceInput,
)
from app.schemas.social import ReportInput


async def approved_creator(db, email: str):
    user, _ = await accounts.register(db, email, "strong-password-123", None, adult_confirmed=True)
    profile = await creators.get_or_create_profile(db, user)
    await creators.update_profile(
        db, profile, {"username": email.split("@")[0], "display_name": "Creator"}, user.id
    )
    await creators.submit(db, profile, user.id)
    await creators.development_verify(db, profile, True, user.id)
    await creators.set_status(db, profile, CreatorStatus.approved, user.id)
    await creators.update_profile(db, profile, {"is_public": True}, user.id)
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


async def account_balance(
    db,
    kind: LedgerAccountKind,
    *,
    creator_id=None,
    group_id=None,
    user_id=None,
) -> int:
    query = select(LedgerAccount).where(
        LedgerAccount.kind == kind,
        LedgerAccount.currency == "EUR",
    )
    query = query.where(
        LedgerAccount.owner_creator_id == creator_id
        if creator_id
        else LedgerAccount.owner_creator_id.is_(None),
        LedgerAccount.owner_group_id == group_id
        if group_id
        else LedgerAccount.owner_group_id.is_(None),
        LedgerAccount.owner_user_id == user_id
        if user_id
        else LedgerAccount.owner_user_id.is_(None),
        LedgerAccount.owner_affiliate_partner_id.is_(None),
    )
    account = await db.scalar(query)
    if account is None:
        return 0
    entries = (
        await db.scalars(
            select(LedgerEntry)
            .where(LedgerEntry.ledger_account_id == account.id)
            .order_by(LedgerEntry.created_at, LedgerEntry.id)
        )
    ).all()
    return sum(
        entry.amount_minor if entry.direction is LedgerDirection.credit else -entry.amount_minor
        for entry in entries
    )


async def marketplace_referred_buyer(db, suffix: str):
    referrer, _ = await accounts.register(
        db, f"market-referrer-{suffix}@example.com", "strong-password-123", None
    )
    program = await referrals.create_program(
        db,
        actor_type=ReferralActorType.user,
        program_type=ReferralProgramType.user_user_referral,
        owner_user_id=referrer.id,
    )
    policy = await referrals.create_policy(
        db,
        program,
        basis_points=2_500,
        eligible_revenue_types=["marketplace"],
    )
    code = f"marketplace-{suffix}"
    await referrals.create_link(db, program, policy, code=code, destination_path="/marketplace")
    _link, token = await referrals.resolve_click(db, code, f"marketplace-session-{suffix}")
    buyer, _ = await accounts.register(
        db, f"market-buyer-{suffix}@example.com", "strong-password-123", None
    )
    assert await referrals.snapshot_signup_attribution(db, buyer, token)
    return referrer, buyer


async def marketplace_creator_referred_buyer(db, suffix: str):
    referrer_user, referrer_creator = await approved_creator(
        db, f"market-referrer-creator-{suffix}@example.com"
    )
    program = await referrals.create_program(
        db,
        actor_type=ReferralActorType.creator,
        program_type=ReferralProgramType.creator_buyer_referral,
        owner_creator_id=referrer_creator.id,
    )
    policy = await referrals.create_policy(
        db,
        program,
        basis_points=2_500,
        eligible_revenue_types=["marketplace"],
    )
    code = f"marketplace-creator-{suffix}"
    await referrals.create_link(db, program, policy, code=code, destination_path="/marketplace")
    _link, token = await referrals.resolve_click(db, code, f"marketplace-creator-session-{suffix}")
    buyer, _ = await accounts.register(
        db, f"market-creator-buyer-{suffix}@example.com", "strong-password-123", None
    )
    assert await referrals.snapshot_signup_attribution(db, buyer, token)
    return referrer_user, referrer_creator, buyer


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

    buyer.adult_attested_at = None
    buyer.adult_attestation_version = None
    with pytest.raises(marketplace.MarketplaceError, match="adult self-attestation"):
        await marketplace.initiate_order(
            db_session, buyer, row.id, 1, "AA", "marketplace-unattested"
        )
    assert row.quantity_available == 10
    assert await db_session.scalar(select(PaymentAttempt.id)) is None
    adult_access.attest_account(buyer)
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
async def test_order_idempotency_replays_after_last_unit_is_reserved(db_session):
    creator_user, creator = await approved_creator(db_session, "replay-market-creator@example.com")
    buyer, _ = await accounts.register(
        db_session, "replay-market-buyer@example.com", "strong-password-123", None
    )
    await marketplace_commission(db_session)
    await allowance(db_session, 100, "AC")
    row = await listing(db_session, creator, creator_user, shipping=100)
    row.quantity_available = 1

    first = await marketplace.initiate_order(db_session, buyer, row.id, 1, "AC", "last-unit-replay")
    replay = await marketplace.initiate_order(
        db_session, buyer, row.id, 1, "AC", "last-unit-replay"
    )

    assert replay.id == first.id
    assert row.quantity_available == 0
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(PaymentAttempt)
            .where(PaymentAttempt.idempotency_key == "last-unit-replay")
        )
        == 1
    )


@pytest.mark.parametrize("creator_blocks_buyer", [False, True])
@pytest.mark.asyncio
async def test_blocked_marketplace_checkout_does_not_reserve_or_attempt_payment(
    db_session, creator_blocks_buyer
):
    creator_user, creator = await approved_creator(
        db_session, f"checkout-block-creator-{creator_blocks_buyer}@example.com"
    )
    buyer, _ = await accounts.register(
        db_session,
        f"checkout-block-buyer-{creator_blocks_buyer}@example.com",
        "strong-password-123",
        None,
    )
    await allowance(db_session, 100, "AD")
    row = await listing(db_session, creator, creator_user, shipping=100)
    db_session.add(
        UserBlock(
            blocker_user_id=creator_user.id if creator_blocks_buyer else buyer.id,
            blocked_user_id=buyer.id if creator_blocks_buyer else creator_user.id,
        )
    )
    await db_session.flush()

    with pytest.raises(marketplace.MarketplaceError, match="not available"):
        await marketplace.initiate_order(db_session, buyer, row.id, 1, "AD", "blocked-checkout")
    assert row.quantity_available == 10
    assert await db_session.scalar(select(PaymentAttempt.id)) is None


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
    with pytest.raises(marketplace.MarketplaceTerminalPaymentError, match="new Idempotency-Key"):
        await marketplace.initiate_order(
            db_session, buyer, failed_listing.id, 1, "AI", "reservation-failed"
        )
    assert failed_listing.quantity_available == 1
    assert (
        await finance.process_development_webhook(db_session, failure_payload, failure_signature)
        is None
    )
    assert failed_listing.quantity_available == 1
    retried_order = await marketplace.initiate_order(
        db_session, buyer, failed_listing.id, 1, "AI", "reservation-retry"
    )
    assert retried_order.id != failed_order.id
    assert retried_order.status is MarketplaceOrderStatus.awaiting_payment
    assert failed_listing.quantity_available == 0
    assert (
        await marketplace.initiate_order(
            db_session, buyer, failed_listing.id, 1, "AI", "reservation-retry"
        )
    ).id == retried_order.id
    retried_attempt = await db_session.get(PaymentAttempt, retried_order.payment_attempt_id)
    assert retried_attempt
    retry_payload, retry_signature = finance.development_webhook_payload(retried_attempt)
    await finance.process_development_webhook(db_session, retry_payload, retry_signature)
    assert retried_order.status is MarketplaceOrderStatus.paid

    # The released reservation remains terminal even if its provider capture
    # arrives after the deliberately new order has paid. The extra cash is
    # frozen as a balanced refund liability, never a second order settlement.
    late_payload, late_signature = finance.development_webhook_payload(failed_attempt)
    await finance.process_development_webhook(db_session, late_payload, late_signature)
    assert failed_order.status is MarketplaceOrderStatus.cancelled
    assert retried_order.status is MarketplaceOrderStatus.paid
    assert failed_listing.quantity_available == 0
    late_requirement = await db_session.scalar(
        select(PaymentRefundRequirement).where(
            PaymentRefundRequirement.payment_attempt_id == failed_attempt.id
        )
    )
    assert late_requirement
    assert (
        late_requirement.source_type,
        late_requirement.source_reference,
        late_requirement.amount_minor,
        late_requirement.status.value,
    ) == (
        ExcessCaptureSource.marketplace_order,
        str(failed_order.id),
        failed_attempt.amount_minor,
        "required",
    )
    liability = await db_session.get(
        LedgerTransaction, late_requirement.liability_ledger_transaction_id
    )
    assert liability
    assert liability.transaction_type is LedgerTransactionType.excess_capture_liability
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(LedgerTransaction)
            .where(LedgerTransaction.idempotency_key == f"marketplace-order:{retried_order.id}")
        )
        == 1
    )
    assert (
        await finance.process_development_webhook(db_session, late_payload, late_signature) is None
    )

    expired_listing = await listing(db_session, creator, creator_user, shipping=100)
    expired_listing.quantity_available = 1
    expired_order = await marketplace.initiate_order(
        db_session, buyer, expired_listing.id, 1, "AI", "reservation-expired"
    )
    expired_attempt = await db_session.get(PaymentAttempt, expired_order.payment_attempt_id)
    assert expired_attempt
    expired_order.reservation_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await marketplace.expire_marketplace_reservations(db_session) == 1
    assert expired_order.status is MarketplaceOrderStatus.cancelled
    assert expired_listing.quantity_available == 1
    assert await marketplace.expire_marketplace_reservations(db_session) == 0
    expired_success, expired_signature = finance.development_webhook_payload(expired_attempt)
    await finance.process_development_webhook(db_session, expired_success, expired_signature)
    assert expired_order.status is MarketplaceOrderStatus.cancelled
    assert await db_session.scalar(
        select(PaymentRefundRequirement.id).where(
            PaymentRefundRequirement.payment_attempt_id == expired_attempt.id
        )
    )

    success_first_listing = await listing(db_session, creator, creator_user, shipping=100)
    success_first_listing.quantity_available = 1
    success_first = await marketplace.initiate_order(
        db_session, buyer, success_first_listing.id, 1, "AI", "reservation-success-first"
    )
    success_first_attempt = await db_session.get(PaymentAttempt, success_first.payment_attempt_id)
    assert success_first_attempt
    success_first.reservation_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    success_payload, success_signature = finance.development_webhook_payload(success_first_attempt)
    await finance.process_development_webhook(db_session, success_payload, success_signature)
    assert success_first.status is MarketplaceOrderStatus.paid
    assert await marketplace.expire_marketplace_reservations(db_session) == 0
    assert success_first_listing.quantity_available == 0


@pytest.mark.asyncio
async def test_expiry_and_admin_reversal_serialize_before_provider_callbacks(
    db_session, monkeypatch
):
    creator_user, creator = await approved_creator(
        db_session, "marketplace-lock-order-creator@example.com"
    )
    buyer, _ = await accounts.register(
        db_session, "marketplace-lock-order-buyer@example.com", "strong-password-123", None
    )
    admin, _ = await accounts.register(
        db_session, "marketplace-lock-order-admin@example.com", "strong-password-123", None
    )
    await marketplace_commission(db_session)
    await allowance(db_session, 100, "AI")

    expiring_listing = await listing(db_session, creator, creator_user, shipping=100)
    expiring_listing.quantity_available = 1
    expiring_order = await marketplace.initiate_order(
        db_session,
        buyer,
        expiring_listing.id,
        1,
        "AI",
        "marketplace-expiry-provider-race",
    )
    expiring_order.reservation_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    expiring_attempt = await db_session.get(PaymentAttempt, expiring_order.payment_attempt_id)
    assert expiring_attempt
    expiry_success, expiry_success_signature = finance.development_webhook_payload(expiring_attempt)

    reversible_listing = await listing(db_session, creator, creator_user, shipping=100)
    reversible_order = await marketplace.initiate_order(
        db_session,
        buyer,
        reversible_listing.id,
        1,
        "AI",
        "marketplace-admin-provider-race",
    )
    reversible_attempt = await db_session.get(PaymentAttempt, reversible_order.payment_attempt_id)
    assert reversible_attempt
    reversible_attempt.status = PaymentStatus.succeeded
    reversible_attempt.completed_at = datetime.now(UTC)
    await marketplace.settle_order(db_session, reversible_order)
    chargeback_payload = json.dumps(
        {
            "id": f"marketplace-admin-race-chargeback-{reversible_attempt.id}",
            "type": "payment.chargeback",
            "payment_reference": reversible_attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    chargeback_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(), chargeback_payload, hashlib.sha256
    ).hexdigest()
    await db_session.commit()

    original_lock = marketplace._lock_order_payment_attempt

    async def run_expiry() -> int:
        async with SessionLocal() as session:
            count = await marketplace.expire_marketplace_reservations(session)
            await session.commit()
            return count

    async def run_webhook(payload: bytes, signature: str) -> None:
        async with SessionLocal() as session:
            await finance.process_development_webhook(session, payload, signature)
            await session.commit()

    expiry_locked = asyncio.Event()
    allow_expiry = asyncio.Event()

    async def pause_expiry_after_attempt_and_order_lock(db, order_id):
        result = await original_lock(db, order_id)
        if order_id == expiring_order.id and not expiry_locked.is_set():
            expiry_locked.set()
            await asyncio.wait_for(allow_expiry.wait(), timeout=5)
        return result

    monkeypatch.setattr(
        marketplace, "_lock_order_payment_attempt", pause_expiry_after_attempt_and_order_lock
    )
    expiry_task = asyncio.create_task(run_expiry())
    await asyncio.wait_for(expiry_locked.wait(), timeout=5)
    expiry_webhook_task = asyncio.create_task(run_webhook(expiry_success, expiry_success_signature))
    await asyncio.sleep(0.05)
    assert not expiry_webhook_task.done()
    allow_expiry.set()
    assert await asyncio.wait_for(expiry_task, timeout=5) == 1
    await asyncio.wait_for(expiry_webhook_task, timeout=5)

    async with SessionLocal() as verification:
        expired = await verification.get(type(expiring_order), expiring_order.id)
        expired_attempt = await verification.get(PaymentAttempt, expiring_attempt.id)
        restored_listing = await verification.get(type(expiring_listing), expiring_listing.id)
        assert expired and expired.status is MarketplaceOrderStatus.cancelled
        assert expired_attempt and expired_attempt.status is PaymentStatus.succeeded
        assert restored_listing and restored_listing.quantity_available == 1
        assert await verification.scalar(
            select(PaymentRefundRequirement.id).where(
                PaymentRefundRequirement.payment_attempt_id == expiring_attempt.id
            )
        )

    monkeypatch.setattr(marketplace, "_lock_order_payment_attempt", original_lock)
    admin_locked = asyncio.Event()
    allow_admin = asyncio.Event()

    async def pause_admin_after_attempt_and_order_lock(db, order_id):
        result = await original_lock(db, order_id)
        if order_id == reversible_order.id and not admin_locked.is_set():
            admin_locked.set()
            await asyncio.wait_for(allow_admin.wait(), timeout=5)
        return result

    monkeypatch.setattr(
        marketplace, "_lock_order_payment_attempt", pause_admin_after_attempt_and_order_lock
    )

    async def run_admin_refund() -> None:
        async with SessionLocal() as session:
            actor = await session.get(User, admin.id)
            assert actor
            await marketplace.refund_order(
                session, reversible_order.id, actor, "admin_provider_lock_order"
            )
            await session.commit()

    admin_task = asyncio.create_task(run_admin_refund())
    await asyncio.wait_for(admin_locked.wait(), timeout=5)
    chargeback_task = asyncio.create_task(run_webhook(chargeback_payload, chargeback_signature))
    await asyncio.sleep(0.05)
    assert not chargeback_task.done()
    allow_admin.set()
    await asyncio.wait_for(admin_task, timeout=5)
    await asyncio.wait_for(chargeback_task, timeout=5)

    async with SessionLocal() as verification:
        reversed_order = await verification.get(type(reversible_order), reversible_order.id)
        reversed_attempt = await verification.get(PaymentAttempt, reversible_attempt.id)
        assert reversed_order and reversed_order.status is MarketplaceOrderStatus.chargeback
        assert reversed_attempt and reversed_attempt.status is PaymentStatus.chargeback
        assert (
            await verification.scalar(
                select(func.count())
                .select_from(LedgerTransaction)
                .where(
                    LedgerTransaction.reversal_of_transaction_id
                    == reversible_order.ledger_transaction_id
                )
            )
            == 1
        )


@pytest.mark.asyncio
async def test_checkout_route_identifies_canonical_terminal_payment_for_safe_key_rotation(
    db_session,
):
    creator_user, creator = await approved_creator(db_session, "terminal-route-creator@example.com")
    buyer, _ = await accounts.register(
        db_session, "terminal-route-buyer@example.com", "strong-password-123", None
    )
    await marketplace_commission(db_session)
    await allowance(db_session, 100, "AQ")
    row = await listing(db_session, creator, creator_user, shipping=100)
    row.quantity_available = 1
    order = await marketplace.initiate_order(db_session, buyer, row.id, 1, "AQ", "terminal-route")
    attempt = await db_session.get(PaymentAttempt, order.payment_attempt_id)
    assert attempt
    failure_payload = json.dumps(
        {
            "id": f"failed-{attempt.id}",
            "type": "payment.failed",
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    failure_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(),
        failure_payload,
        hashlib.sha256,
    ).hexdigest()
    await finance.process_development_webhook(db_session, failure_payload, failure_signature)
    public_id, listing_id, order_id = row.public_id, row.id, order.id
    await db_session.commit()

    checkout = MarketplaceCheckoutInput(
        quantity=1,
        destination_country_code="AQ",
        shipping_address=MarketplaceShippingAddressInput(
            recipient_name="Buyer",
            line1="1 Retry Road",
            city="Retry City",
            postal_code="1000",
            country_code="AQ",
        ),
    )
    with pytest.raises(HTTPException) as terminal:
        await marketplace_routes.checkout(
            public_id, checkout, (buyer, None), db_session, "terminal-route"
        )
    assert terminal.value.status_code == 409
    assert terminal.value.detail == {
        "code": "marketplace_payment_terminal",
        "message": "Marketplace order payment failed; retry with a new Idempotency-Key",
        "order_id": str(order_id),
        "status": "cancelled",
    }
    persisted = await db_session.get(type(order), order_id)
    listing_after = await db_session.get(MarketplaceListing, listing_id)
    assert persisted and persisted.status is MarketplaceOrderStatus.cancelled
    assert listing_after and listing_after.quantity_available == 1


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
@pytest.mark.parametrize(
    "prior_status",
    [
        MarketplaceOrderStatus.paid,
        MarketplaceOrderStatus.processing,
        MarketplaceOrderStatus.shipped,
        MarketplaceOrderStatus.delivered,
    ],
)
async def test_seller_favour_restores_exact_pre_dispute_order_lifecycle(db_session, prior_status):
    creator_user, creator = await approved_creator(
        db_session, f"lifecycle-{prior_status.value}-creator@example.com"
    )
    buyer, _ = await accounts.register(
        db_session,
        f"lifecycle-{prior_status.value}-buyer@example.com",
        "strong-password-123",
        None,
    )
    admin, _ = await accounts.register(
        db_session,
        f"lifecycle-{prior_status.value}-admin@example.com",
        "strong-password-123",
        None,
    )
    await accounts.assign_role(db_session, admin, "admin", admin.id, None)
    await marketplace_commission(db_session)
    await allowance(db_session, 100, "AO")
    row = await listing(db_session, creator, creator_user, shipping=100)
    order = await marketplace.initiate_order(
        db_session,
        buyer,
        row.id,
        1,
        "AO",
        f"lifecycle-{prior_status.value}",
    )
    attempt = await db_session.get(PaymentAttempt, order.payment_attempt_id)
    assert attempt
    paid_payload, paid_signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db_session, paid_payload, paid_signature)
    if prior_status is MarketplaceOrderStatus.processing:
        await marketplace.mark_order_processing(db_session, order.id, creator_user, creator.id)
    elif prior_status in {MarketplaceOrderStatus.shipped, MarketplaceOrderStatus.delivered}:
        await marketplace.mark_order_shipped(
            db_session,
            order.id,
            creator_user,
            creator.id,
            "CTT",
            f"TRACK-{prior_status.value}",
        )
        if prior_status is MarketplaceOrderStatus.delivered:
            await marketplace.confirm_order_delivery(db_session, order.id, buyer)
    assert order.status is prior_status
    lifecycle_facts = (
        order.paid_at,
        order.shipped_at,
        order.delivered_at,
        order.carrier,
        order.tracking_reference,
        order.earnings_hold_until,
    )

    await marketplace.open_order_dispute(db_session, order.id, buyer, "lifecycle review")
    assert order.status is MarketplaceOrderStatus.disputed
    dispute_event = await db_session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.event_type == "marketplace.order_disputed",
            AuditEvent.target_id == str(order.id),
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
    )
    assert dispute_event
    assert dispute_event.metadata_json["prior_order_status"] == prior_status.value

    await marketplace.resolve_order_dispute(db_session, order.id, admin, False, "seller won")
    assert order.status is prior_status
    assert (
        order.paid_at,
        order.shipped_at,
        order.delivered_at,
        order.carrier,
        order.tracking_reference,
        order.earnings_hold_until,
    ) == lifecycle_facts
    if prior_status in {MarketplaceOrderStatus.paid, MarketplaceOrderStatus.processing}:
        assert order.shipped_at is None
        assert order.delivered_at is None
        assert order.carrier is None
        assert order.tracking_reference is None
    resolution_event = await db_session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.event_type == "marketplace.dispute_resolved",
            AuditEvent.target_id == str(order.id),
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
    )
    assert resolution_event
    assert resolution_event.metadata_json["dispute_audit_event_id"] == str(dispute_event.id)
    assert resolution_event.metadata_json["restored_order_status"] == prior_status.value


@pytest.mark.asyncio
async def test_marketplace_restore_never_reclassifies_older_pending_ppv_release(
    db_session, monkeypatch
):
    creator_user, creator = await approved_creator(
        db_session, "cross-domain-release-creator@example.com"
    )
    ppv_buyer, _ = await accounts.register(
        db_session, "cross-domain-release-ppv-buyer@example.com", "strong-password-123", None
    )
    marketplace_buyer, _ = await accounts.register(
        db_session,
        "cross-domain-release-marketplace-buyer@example.com",
        "strong-password-123",
        None,
    )
    admin, _ = await accounts.register(
        db_session, "cross-domain-release-admin@example.com", "strong-password-123", None
    )
    await accounts.assign_role(db_session, admin, "admin", admin.id, None)

    content = ContentItem(
        owner_creator_id=creator.id,
        created_by_user_id=creator_user.id,
        content_type=ContentType.gallery,
        title="Older pending PPV",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=100,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    ppv = await finance.initiate_purchase(
        db_session, ppv_buyer, content.id, "cross-domain-pending-ppv"
    )
    ppv_attempt = await db_session.get(PaymentAttempt, ppv.payment_attempt_id)
    assert ppv_attempt
    ppv_payload, ppv_signature = finance.development_webhook_payload(ppv_attempt)
    settled_ppv = await finance.process_development_webhook(db_session, ppv_payload, ppv_signature)
    assert settled_ppv and settled_ppv.id == ppv.id
    assert ppv.ledger_transaction_id

    await marketplace_commission(db_session)
    await allowance(db_session, 100, "AQ")
    market_listing = await listing(db_session, creator, creator_user, shipping=100)
    order = await marketplace.initiate_order(
        db_session,
        marketplace_buyer,
        market_listing.id,
        1,
        "AQ",
        "cross-domain-marketplace-order",
    )
    market_attempt = await db_session.get(PaymentAttempt, order.payment_attempt_id)
    assert market_attempt
    market_payload, market_signature = finance.development_webhook_payload(market_attempt)
    await finance.process_development_webhook(db_session, market_payload, market_signature)
    await marketplace.mark_order_shipped(
        db_session, order.id, creator_user, creator.id, "CTT", "CROSS-DOMAIN"
    )
    await marketplace.confirm_order_delivery(db_session, order.id, marketplace_buyer)
    order.earnings_hold_until = datetime.now(UTC) - timedelta(seconds=1)
    assert await marketplace.release_order_earnings(db_session, order)
    await marketplace.open_order_dispute(
        db_session, order.id, marketplace_buyer, "cross-domain dispute"
    )
    await marketplace.resolve_order_dispute(
        db_session, order.id, admin, False, "seller-favour restoration"
    )
    assert await finance.creator_balances(db_session, creator.id, "EUR") == {
        "pending_amount_minor": 80,
        "available_amount_minor": 500,
    }
    restore = await db_session.scalar(
        select(LedgerTransaction).where(
            LedgerTransaction.transaction_type == LedgerTransactionType.earnings_release,
            LedgerTransaction.metadata_json["marketplace_order_id"].astext == str(order.id),
            LedgerTransaction.metadata_json["marketplace_dispute_operation"].astext == "restore",
        )
    )
    assert restore
    assert restore.metadata_json["creator_id"] == str(creator.id)

    dispute_payload = json.dumps(
        {
            "id": f"cross-domain-ppv-dispute-{ppv_attempt.id}",
            "type": "payment.disputed",
            "payment_reference": ppv_attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    dispute_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(),
        dispute_payload,
        hashlib.sha256,
    ).hexdigest()
    await finance.process_development_webhook(db_session, dispute_payload, dispute_signature)
    # The Marketplace restoration is not evidence that this older PPV was
    # generically released. Opening its provider dispute therefore moves no
    # value out of Marketplace available funds.
    assert await finance.creator_balances(db_session, creator.id, "EUR") == {
        "pending_amount_minor": 80,
        "available_amount_minor": 500,
    }
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(LedgerTransaction)
            .where(
                LedgerTransaction.transaction_type == LedgerTransactionType.payment_dispute_hold,
                LedgerTransaction.metadata_json["original_ledger_transaction_id"].astext
                == str(ppv.ledger_transaction_id),
            )
        )
        == 0
    )

    chargeback_payload = json.dumps(
        {
            "id": f"cross-domain-ppv-chargeback-{ppv_attempt.id}",
            "type": "payment.chargeback",
            "payment_reference": ppv_attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    chargeback_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(),
        chargeback_payload,
        hashlib.sha256,
    ).hexdigest()
    await finance.process_development_webhook(db_session, chargeback_payload, chargeback_signature)
    await finance.process_development_webhook(db_session, chargeback_payload, chargeback_signature)
    assert await finance.creator_balances(db_session, creator.id, "EUR") == {
        "pending_amount_minor": 0,
        "available_amount_minor": 500,
    }
    chargeback = await db_session.scalar(
        select(LedgerTransaction).where(
            LedgerTransaction.transaction_type == LedgerTransactionType.chargeback,
            LedgerTransaction.reversal_of_transaction_id == ppv.ledger_transaction_id,
        )
    )
    assert chargeback
    creator_debits = (
        await db_session.execute(
            select(LedgerAccount.kind, LedgerEntry.direction, LedgerEntry.amount_minor)
            .join(LedgerEntry, LedgerEntry.ledger_account_id == LedgerAccount.id)
            .where(
                LedgerEntry.transaction_id == chargeback.id,
                LedgerAccount.owner_creator_id == creator.id,
            )
        )
    ).all()
    assert creator_debits == [(LedgerAccountKind.creator_pending, LedgerDirection.debit, 80)]
    assert await finance.release_creator_earnings(db_session, creator.id, "EUR") is None

    later_buyer, _ = await accounts.register(
        db_session, "cross-domain-release-later-buyer@example.com", "strong-password-123", None
    )
    later_ppv = await finance.initiate_purchase(
        db_session, later_buyer, content.id, "cross-domain-later-ppv"
    )
    later_attempt = await db_session.get(PaymentAttempt, later_ppv.payment_attempt_id)
    assert later_attempt
    later_payload, later_signature = finance.development_webhook_payload(later_attempt)
    await finance.process_development_webhook(db_session, later_payload, later_signature)
    monkeypatch.setattr(
        finance, "get_settings", lambda: Settings(creator_earnings_settlement_seconds=0)
    )
    generic_release = await finance.release_creator_earnings(db_session, creator.id, "EUR")
    assert generic_release
    assert generic_release.idempotency_key == f"release:{creator.id}:EUR:1"
    assert generic_release.metadata_json["release_provenance"] == "generic_creator_settlement"
    assert await finance.release_creator_earnings(db_session, creator.id, "EUR") is None
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(LedgerTransaction)
            .where(LedgerTransaction.idempotency_key.like(f"release:{creator.id}:EUR:%"))
        )
        == 1
    )


@pytest.mark.asyncio
async def test_released_dispute_holds_only_order_a_frozen_group_and_referral_allocations(
    db_session,
):
    manager, _ = await accounts.register(
        db_session, "hold-a-manager@example.com", "strong-password-123", None
    )
    creator_user, creator = await approved_creator(db_session, "hold-a-creator@example.com")
    referrer_user, referrer_creator, buyer = await marketplace_creator_referred_buyer(
        db_session, "hold-a"
    )
    admin, _ = await accounts.register(
        db_session, "hold-a-admin@example.com", "strong-password-123", None
    )
    await accounts.assign_role(db_session, admin, "admin", admin.id, None)
    group = await groups.create_group(
        db_session, manager, "Hold A group", "hold-a-group", 5_000, None
    )
    membership = await groups.invite_creator(
        db_session,
        group.id,
        manager,
        creator.id,
        5_000,
        [GroupPermission.manage_marketplace],
    )
    await groups.accept_invitation(db_session, membership.id, creator_user)
    contract = await groups.active_contract(db_session, creator.id)
    assert contract
    await marketplace_commission(db_session)
    await allowance(db_session, 100, "AL")

    listing_a = await listing(db_session, creator, creator_user, shipping=100)
    listing_b = await listing(db_session, creator, creator_user, shipping=100)
    order_a = await marketplace.initiate_order(
        db_session, buyer, listing_a.id, 1, "AL", "released-order-a"
    )
    order_b = await marketplace.initiate_order(
        db_session, buyer, listing_b.id, 1, "AL", "pending-order-b"
    )
    for order in (order_a, order_b):
        attempt = await db_session.get(PaymentAttempt, order.payment_attempt_id)
        assert attempt
        payload, signature = finance.development_webhook_payload(attempt)
        await finance.process_development_webhook(db_session, payload, signature)
    await marketplace.mark_order_shipped(
        db_session, order_a.id, creator_user, creator.id, "CTT", "HOLD-A"
    )
    await marketplace.confirm_order_delivery(db_session, order_a.id, buyer)
    order_a.earnings_hold_until = datetime.now(UTC) - timedelta(seconds=1)
    assert await marketplace.release_order_earnings(db_session, order_a)
    released_at = order_a.earnings_released_at
    assert released_at

    assert await finance.creator_balances(db_session, creator.id, "EUR") == {
        "pending_amount_minor": 300,
        "available_amount_minor": 300,
    }
    assert (
        await account_balance(db_session, LedgerAccountKind.group_pending, group_id=group.id) == 200
    )
    assert (
        await account_balance(db_session, LedgerAccountKind.group_available, group_id=group.id)
        == 200
    )
    assert (
        await account_balance(
            db_session,
            LedgerAccountKind.referrer_pending,
            creator_id=referrer_creator.id,
        )
        == 25
    )
    assert (
        await account_balance(
            db_session,
            LedgerAccountKind.referrer_available,
            creator_id=referrer_creator.id,
        )
        == 25
    )

    await marketplace.open_order_dispute(db_session, order_a.id, buyer, "parcel disputed")
    await marketplace.open_order_dispute(db_session, order_a.id, buyer, "replayed command")
    assert order_a.earnings_released_at == released_at
    assert order_a.earnings_release_status is MarketplaceEarningsReleaseStatus.blocked
    assert await finance.creator_balances(db_session, creator.id, "EUR") == {
        "pending_amount_minor": 600,
        "available_amount_minor": 0,
    }
    assert (
        await account_balance(db_session, LedgerAccountKind.group_pending, group_id=group.id) == 400
    )
    assert (
        await account_balance(db_session, LedgerAccountKind.group_available, group_id=group.id) == 0
    )
    assert (
        await account_balance(
            db_session,
            LedgerAccountKind.referrer_pending,
            creator_id=referrer_creator.id,
        )
        == 50
    )
    assert (
        await account_balance(
            db_session,
            LedgerAccountKind.referrer_available,
            creator_id=referrer_creator.id,
        )
        == 0
    )
    held_dashboard = await referrals.dashboard(db_session, referrer_user.id)
    assert held_dashboard["totals_by_currency"]["EUR"] == {
        "pending_amount_minor": 50,
        "available_amount_minor": 0,
        "reversed_amount_minor": 0,
    }
    assert sorted(row["availability_status"] for row in held_dashboard["allocations"]) == [
        "pending",
        "pending",
    ]
    held_metrics = await analytics.creator_referral_metrics(
        db_session,
        referrer_creator.id,
        datetime.now(UTC) - timedelta(hours=1),
        datetime.now(UTC) + timedelta(hours=1),
    )
    assert held_metrics["currencies"] == [
        {
            "currency": "EUR",
            "referral_earnings_minor": 50,
            "pending_minor": 50,
            "available_minor": 0,
            "reversed_minor": 0,
            "attributed_volume_minor": 200,
        }
    ]

    holds = (
        await db_session.scalars(
            select(LedgerTransaction).where(
                LedgerTransaction.transaction_type == LedgerTransactionType.payment_dispute_hold,
                LedgerTransaction.metadata_json["marketplace_order_id"].astext == str(order_a.id),
            )
        )
    ).all()
    assert len(holds) == 1
    hold = holds[0]
    release = await db_session.scalar(
        select(LedgerTransaction).where(
            LedgerTransaction.idempotency_key == f"marketplace-release:{order_a.id}"
        )
    )
    assert release and hold.reversal_of_transaction_id == release.id
    assert hold.metadata_json["group_contract_id"] == str(contract.id)
    assert hold.metadata_json["creator_amount_minor"] == "300"
    assert hold.metadata_json["group_amount_minor"] == "200"
    assert hold.metadata_json["referral_amount_minor"] == "25"
    hold_rows = await db_session.execute(
        select(LedgerAccount.kind, LedgerEntry.direction, LedgerEntry.amount_minor)
        .join(LedgerEntry, LedgerEntry.ledger_account_id == LedgerAccount.id)
        .where(LedgerEntry.transaction_id == hold.id)
    )
    assert {(kind, direction): amount for kind, direction, amount in hold_rows} == {
        (LedgerAccountKind.creator_available, LedgerDirection.debit): 300,
        (LedgerAccountKind.creator_pending, LedgerDirection.credit): 300,
        (LedgerAccountKind.group_available, LedgerDirection.debit): 200,
        (LedgerAccountKind.group_pending, LedgerDirection.credit): 200,
        (LedgerAccountKind.referrer_available, LedgerDirection.debit): 25,
        (LedgerAccountKind.referrer_pending, LedgerDirection.credit): 25,
    }

    await marketplace.resolve_order_dispute(db_session, order_a.id, admin, False, "seller won")
    await marketplace.resolve_order_dispute(
        db_session, order_a.id, admin, False, "replayed seller resolution"
    )
    assert order_a.earnings_released_at == released_at
    assert order_a.earnings_release_status is MarketplaceEarningsReleaseStatus.released
    assert order_b.status is MarketplaceOrderStatus.paid
    assert order_b.earnings_release_status is MarketplaceEarningsReleaseStatus.pending
    assert await finance.creator_balances(db_session, creator.id, "EUR") == {
        "pending_amount_minor": 300,
        "available_amount_minor": 300,
    }
    assert (
        await account_balance(db_session, LedgerAccountKind.group_pending, group_id=group.id) == 200
    )
    assert (
        await account_balance(db_session, LedgerAccountKind.group_available, group_id=group.id)
        == 200
    )
    assert (
        await account_balance(
            db_session,
            LedgerAccountKind.referrer_pending,
            creator_id=referrer_creator.id,
        )
        == 25
    )
    assert (
        await account_balance(
            db_session,
            LedgerAccountKind.referrer_available,
            creator_id=referrer_creator.id,
        )
        == 25
    )
    restored_dashboard = await referrals.dashboard(db_session, referrer_user.id)
    assert restored_dashboard["totals_by_currency"]["EUR"] == {
        "pending_amount_minor": 25,
        "available_amount_minor": 25,
        "reversed_amount_minor": 0,
    }
    assert sorted(row["availability_status"] for row in restored_dashboard["allocations"]) == [
        "available",
        "pending",
    ]
    restored_metrics = await analytics.creator_referral_metrics(
        db_session,
        referrer_creator.id,
        datetime.now(UTC) - timedelta(hours=1),
        datetime.now(UTC) + timedelta(hours=1),
    )
    assert restored_metrics["currencies"][0]["pending_minor"] == 25
    assert restored_metrics["currencies"][0]["available_minor"] == 25
    restores = (
        await db_session.scalars(
            select(LedgerTransaction).where(
                LedgerTransaction.transaction_type == LedgerTransactionType.earnings_release,
                LedgerTransaction.reversal_of_transaction_id == hold.id,
            )
        )
    ).all()
    assert len(restores) == 1
    assert restores[0].metadata_json["marketplace_dispute_operation"] == "restore"


@pytest.mark.asyncio
async def test_provider_chargeback_reverses_one_active_marketplace_dispute_hold_exactly_once(
    db_session,
):
    manager, _ = await accounts.register(
        db_session, "held-chargeback-manager@example.com", "strong-password-123", None
    )
    creator_user, creator = await approved_creator(
        db_session, "held-chargeback-creator@example.com"
    )
    referrer, buyer = await marketplace_referred_buyer(db_session, "held-chargeback")
    group = await groups.create_group(
        db_session, manager, "Held chargeback group", "held-chargeback-group", 5_000, None
    )
    membership = await groups.invite_creator(
        db_session,
        group.id,
        manager,
        creator.id,
        5_000,
        [GroupPermission.manage_marketplace],
    )
    await groups.accept_invitation(db_session, membership.id, creator_user)
    await marketplace_commission(db_session)
    await allowance(db_session, 100, "AM")
    row = await listing(db_session, creator, creator_user, shipping=100)
    order = await marketplace.initiate_order(
        db_session, buyer, row.id, 1, "AM", "held-provider-chargeback"
    )
    attempt = await db_session.get(PaymentAttempt, order.payment_attempt_id)
    assert attempt
    paid_payload, paid_signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db_session, paid_payload, paid_signature)
    await marketplace.mark_order_shipped(
        db_session, order.id, creator_user, creator.id, "CTT", "HELD-CB"
    )
    await marketplace.confirm_order_delivery(db_session, order.id, buyer)
    order.earnings_hold_until = datetime.now(UTC) - timedelta(seconds=1)
    assert await marketplace.release_order_earnings(db_session, order)
    released_at = order.earnings_released_at
    original = await db_session.get(LedgerTransaction, order.ledger_transaction_id)
    assert released_at and original

    disputed_payload = json.dumps(
        {
            "id": f"held-marketplace-dispute-{attempt.id}",
            "type": "payment.disputed",
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    disputed_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(), disputed_payload, hashlib.sha256
    ).hexdigest()
    await finance.process_development_webhook(db_session, disputed_payload, disputed_signature)
    await finance.process_development_webhook(db_session, disputed_payload, disputed_signature)
    assert order.status is MarketplaceOrderStatus.disputed
    assert attempt.status is PaymentStatus.disputed
    assert await finance.creator_balances(db_session, creator.id, "EUR") == {
        "pending_amount_minor": 300,
        "available_amount_minor": 0,
    }
    assert (
        await account_balance(db_session, LedgerAccountKind.group_pending, group_id=group.id) == 200
    )
    assert (
        await account_balance(db_session, LedgerAccountKind.referrer_pending, user_id=referrer.id)
        == 25
    )
    held_dashboard = await referrals.dashboard(db_session, referrer.id)
    assert held_dashboard["totals_by_currency"]["EUR"] == {
        "pending_amount_minor": 25,
        "available_amount_minor": 0,
        "reversed_amount_minor": 0,
    }
    assert held_dashboard["allocations"][0]["availability_status"] == "pending"
    assert held_dashboard["allocations"][0]["released_at"] is not None

    chargeback_payload = json.dumps(
        {
            "id": f"held-marketplace-chargeback-{attempt.id}",
            "type": "payment.chargeback",
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    chargeback_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(), chargeback_payload, hashlib.sha256
    ).hexdigest()
    await finance.process_development_webhook(db_session, chargeback_payload, chargeback_signature)
    await finance.process_development_webhook(db_session, chargeback_payload, chargeback_signature)
    assert order.status is MarketplaceOrderStatus.chargeback
    assert order.earnings_released_at == released_at
    assert attempt.status is PaymentStatus.chargeback
    assert await finance.creator_balances(db_session, creator.id, "EUR") == {
        "pending_amount_minor": 0,
        "available_amount_minor": 0,
    }
    assert (
        await account_balance(db_session, LedgerAccountKind.group_pending, group_id=group.id) == 0
    )
    assert (
        await account_balance(db_session, LedgerAccountKind.group_available, group_id=group.id) == 0
    )
    assert (
        await account_balance(db_session, LedgerAccountKind.referrer_pending, user_id=referrer.id)
        == 0
    )
    assert (
        await account_balance(db_session, LedgerAccountKind.referrer_available, user_id=referrer.id)
        == 0
    )
    reversed_dashboard = await referrals.dashboard(db_session, referrer.id)
    assert reversed_dashboard["totals_by_currency"]["EUR"] == {
        "pending_amount_minor": 0,
        "available_amount_minor": 0,
        "reversed_amount_minor": 25,
    }
    assert reversed_dashboard["allocations"][0]["availability_status"] == "reversed"
    holds = (
        await db_session.scalars(
            select(LedgerTransaction).where(
                LedgerTransaction.transaction_type == LedgerTransactionType.payment_dispute_hold,
                LedgerTransaction.metadata_json["marketplace_order_id"].astext == str(order.id),
            )
        )
    ).all()
    assert len(holds) == 1
    reversals = (
        await db_session.scalars(
            select(LedgerTransaction).where(
                LedgerTransaction.reversal_of_transaction_id == original.id,
                LedgerTransaction.transaction_type.in_(
                    [LedgerTransactionType.refund, LedgerTransactionType.chargeback]
                ),
            )
        )
    ).all()
    assert len(reversals) == 1
    assert reversals[0].transaction_type is LedgerTransactionType.chargeback
    assert reversals[0].metadata_json["dispute_hold_ledger_transaction_id"] == str(holds[0].id)
    assert reversals[0].metadata_json["creator_amount_minor"] == "300"
    assert reversals[0].metadata_json["group_amount_minor"] == "200"
    assert reversals[0].metadata_json["referral_amount_minor"] == "25"


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


async def _ready_marketplace_image(db_session, creator, suffix: str):
    asset = MediaAsset(
        owner_creator_id=creator.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        moderation_status=ModerationStatus.approved,
        audience=MediaAudience.safe_public,
        storage_key=f"original/marketplace-{suffix}-{secrets.token_hex(4)}.jpg",
        original_filename=f"marketplace-{suffix}.jpg",
        mime_type="image/jpeg",
        size_bytes=128,
        width=800,
        height=600,
    )
    db_session.add(asset)
    await db_session.flush()
    derivative = MediaDerivative(
        media_asset_id=asset.id,
        derivative_type=DerivativeType.display,
        status=MediaStatus.ready,
        storage_key=f"derivative/{asset.id}/display.webp",
        mime_type="image/webp",
        size_bytes=64,
        width=800,
        height=600,
    )
    db_session.add(derivative)
    await db_session.flush()
    return asset, derivative


@pytest.mark.asyncio
async def test_public_marketplace_projection_uses_only_dedicated_safe_derivatives(db_session):
    creator_user, creator = await approved_creator(db_session, "public-media-creator@example.com")
    creator.is_public = True
    asset, derivative = await _ready_marketplace_image(db_session, creator, "dedicated")
    created = await marketplace.create_listing(
        db_session,
        creator_user,
        creator_id=creator.id,
        title="Dedicated preview",
        description="Safe listing projection",
        category="prints",
        condition="new",
        quantity_available=2,
        price_amount_minor=1_200,
        currency="EUR",
        shipping_mode="worldwide",
        origin_country_code="PT",
        shipping_charged_minor=200,
        media_asset_ids=[asset.id],
    )
    created.status = MarketplaceListingStatus.published
    created.moderation_status = ModerationStatus.approved

    response = await marketplace_routes.public_listing_response(db_session, created)
    assert response.seller and response.seller.username == creator.username
    assert len(response.media) == 1
    assert response.media[0].delivery_path == f"/media/previews/{derivative.id}"
    assert asset.storage_key not in response.model_dump_json()
    assert derivative.storage_key not in response.model_dump_json()
    assert await can_access_preview(db_session, derivative)


@pytest.mark.asyncio
async def test_paid_content_asset_cannot_become_marketplace_public_media(db_session):
    creator_user, creator = await approved_creator(
        db_session, "exclusive-media-creator@example.com"
    )
    creator.is_public = True
    asset, derivative = await _ready_marketplace_image(db_session, creator, "paid-gallery")
    content = ContentItem(
        owner_creator_id=creator.id,
        created_by_user_id=creator_user.id,
        content_type=ContentType.gallery,
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=900,
        price_currency="EUR",
        title="Protected gallery",
    )
    db_session.add(content)
    await db_session.flush()
    gallery = Gallery(content_id=content.id, cover_media_asset_id=asset.id, preview_count=1)
    db_session.add(gallery)
    await db_session.flush()
    db_session.add(
        GalleryItem(gallery_id=gallery.id, media_asset_id=asset.id, position=0, is_preview=True)
    )
    await db_session.flush()

    with pytest.raises(marketplace.MarketplaceError, match="dedicated to marketplace"):
        await marketplace.create_listing(
            db_session,
            creator_user,
            creator_id=creator.id,
            title="Unsafe reuse",
            description=None,
            category="prints",
            condition="new",
            quantity_available=1,
            price_amount_minor=1_000,
            currency="EUR",
            shipping_mode="worldwide",
            origin_country_code="PT",
            shipping_charged_minor=200,
            media_asset_ids=[asset.id],
        )

    corrupt = await listing(db_session, creator, creator_user, shipping=200)
    db_session.add(
        MarketplaceListingMedia(
            listing_id=corrupt.id,
            media_asset_id=asset.id,
            position=0,
        )
    )
    await db_session.flush()
    response = await marketplace_routes.public_listing_response(db_session, corrupt)
    assert response.media == []
    assert not await can_access_preview(db_session, derivative)


@pytest.mark.parametrize("creator_blocks_viewer", [False, True])
@pytest.mark.asyncio
async def test_direct_marketplace_surfaces_apply_two_way_blocks(db_session, creator_blocks_viewer):
    creator_user, creator = await approved_creator(
        db_session, f"blocked-seller-{creator_blocks_viewer}@example.com"
    )
    creator.is_public = True
    viewer, _ = await accounts.register(
        db_session,
        f"blocked-viewer-{creator_blocks_viewer}@example.com",
        "strong-password-123",
        None,
    )
    row = await listing(db_session, creator, creator_user, shipping=0)
    db_session.add(
        UserBlock(
            blocker_user_id=creator_user.id if creator_blocks_viewer else viewer.id,
            blocked_user_id=viewer.id if creator_blocks_viewer else creator_user.id,
        )
    )
    await db_session.flush()

    anonymous = await marketplace_routes.public_listings(db_session, None)
    assert row.id in {item.id for item in anonymous}
    blocked = await marketplace_routes.public_listings(db_session, (viewer, None))
    assert row.id not in {item.id for item in blocked}
    with pytest.raises(HTTPException) as exc:
        await marketplace_routes.public_listing(row.public_id, db_session, (viewer, None))
    assert exc.value.status_code == 404
