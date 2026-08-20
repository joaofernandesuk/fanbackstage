from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.accounts import service as accounts
from app.content.access import can_access_content
from app.creators import service as creators
from app.finance import service as finance
from app.models.content import (
    AccessPolicy,
    ContentItem,
    ContentStatus,
    ContentType,
    ModerationStatus,
)
from app.models.creator import CreatorStatus
from app.models.finance import PaymentAttempt, PaymentStatus
from app.models.subscription import (
    PromotionEligibility,
    PromotionRenewalScope,
    SubscriptionPeriod,
    SubscriptionPlanPrice,
    SubscriptionPromotion,
    SubscriptionPromotionRule,
    SubscriptionStatus,
)
from app.subscriptions import service as subscriptions


async def creator(db, email: str):
    user, _ = await accounts.register(db, email, "strong-password-123", None)
    profile = await creators.get_or_create_profile(db, user)
    await creators.update_profile(
        db, profile, {"username": email.split("@")[0], "display_name": "Creator"}, user.id
    )
    await creators.submit(db, profile, user.id)
    await creators.development_verify(db, profile, True, user.id)
    await creators.set_status(db, profile, CreatorStatus.approved, user.id)
    return user, profile


@pytest.mark.asyncio
async def test_subscription_snapshots_promotion_posts_ledger_and_grants_creator_scope(db_session):
    owner, profile = await creator(db_session, "sub-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "sub-buyer@example.com", "strong-password-123", None
    )
    await subscriptions.configure_plan(
        db_session,
        profile.id,
        "EUR",
        True,
        [
            {"duration": "month_1", "amount_minor": 999, "enabled": True},
            {"duration": "month_3", "amount_minor": 2500, "enabled": True},
        ],
    )
    now = datetime.now(UTC)
    promotion = SubscriptionPromotion(
        creator_id=profile.id,
        name="Mixed",
        eligibility=PromotionEligibility.new_subscriber,
        renewal_scope=PromotionRenewalScope.initial_only,
        start_at=now - timedelta(minutes=1),
        end_at=now + timedelta(minutes=1),
    )
    db_session.add(promotion)
    await db_session.flush()
    db_session.add_all(
        [
            SubscriptionPromotionRule(
                promotion_id=promotion.id, duration="month_1", discount_basis_points=2000
            ),
            SubscriptionPromotionRule(
                promotion_id=promotion.id, duration="month_3", discount_basis_points=2500
            ),
        ]
    )
    subscription = await subscriptions.create_subscription(
        db_session, buyer, profile.id, "month_1", "sub-start"
    )
    period = await db_session.scalar(
        select(SubscriptionPeriod).where(SubscriptionPeriod.subscription_id == subscription.id)
    )
    assert (
        period
        and period.base_amount_minor == 999
        and period.discount_amount_minor == 199
        and period.charged_amount_minor == 800
    )
    attempt = await db_session.get(PaymentAttempt, period.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db_session, payload, signature)
    assert subscription.status is SubscriptionStatus.active
    assert period.ledger_transaction_id and period.entitlement_id
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Subscriber",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.subscription,
    )
    db_session.add(content)
    await db_session.flush()
    assert await can_access_content(db_session, content, buyer)
    content.access_policy = AccessPolicy.ppv
    assert not await can_access_content(db_session, content, buyer)


@pytest.mark.asyncio
async def test_new_and_reactivation_promotions_are_server_resolved(db_session):
    _owner, profile = await creator(db_session, "promo-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "promo-buyer@example.com", "strong-password-123", None
    )
    await subscriptions.configure_plan(
        db_session,
        profile.id,
        "EUR",
        True,
        [{"duration": "month_1", "amount_minor": 1000, "enabled": True}],
    )
    first = await subscriptions.create_subscription(
        db_session, buyer, profile.id, "month_1", "first"
    )
    period = await db_session.scalar(
        select(SubscriptionPeriod).where(SubscriptionPeriod.subscription_id == first.id)
    )
    assert period
    attempt = await db_session.get(PaymentAttempt, period.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db_session, payload, signature)
    first.status = SubscriptionStatus.expired
    first.current_period_end = datetime.now(UTC) - timedelta(seconds=1)
    second = await subscriptions.create_subscription(
        db_session, buyer, profile.id, "month_1", "reactivate"
    )
    assert second.id != first.id


@pytest.mark.asyncio
async def test_cancel_preserves_access_and_renewal_keeps_selected_duration(db_session):
    _owner, profile = await creator(db_session, "renew-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "renew-buyer@example.com", "strong-password-123", None
    )
    await subscriptions.configure_plan(
        db_session,
        profile.id,
        "EUR",
        True,
        [{"duration": "month_3", "amount_minor": 3000, "enabled": True}],
    )
    subscription = await subscriptions.create_subscription(
        db_session, buyer, profile.id, "month_3", "renew-start"
    )
    period = await db_session.scalar(
        select(SubscriptionPeriod).where(SubscriptionPeriod.subscription_id == subscription.id)
    )
    assert period
    attempt = await db_session.get(PaymentAttempt, period.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db_session, payload, signature)
    await subscriptions.set_auto_renew(db_session, buyer, subscription.id, False)
    assert subscription.cancel_at_period_end and subscription.status is SubscriptionStatus.active
    assert await subscriptions.renew_due_subscriptions(db_session) == 0
    await subscriptions.set_auto_renew(db_session, buyer, subscription.id, True)
    subscription.current_period_end = datetime.now(UTC) - timedelta(seconds=1)
    assert await subscriptions.renew_due_subscriptions(db_session) == 1
    renewal = await db_session.scalar(
        select(SubscriptionPeriod).where(
            SubscriptionPeriod.subscription_id == subscription.id,
            SubscriptionPeriod.sequence == 2,
        )
    )
    assert renewal and renewal.duration.value == "month_3"
    renewal_attempt = await db_session.get(PaymentAttempt, renewal.payment_attempt_id)
    assert renewal_attempt
    renewal_payload, renewal_signature = finance.development_webhook_payload(renewal_attempt)
    await finance.process_development_webhook(db_session, renewal_payload, renewal_signature)
    assert renewal.status.value == "active" and subscription.status is SubscriptionStatus.active


@pytest.mark.asyncio
async def test_failed_renewal_enters_grace_and_preserves_subscription_access(db_session):
    owner, profile = await creator(db_session, "grace-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "grace-buyer@example.com", "strong-password-123", None
    )
    await subscriptions.configure_plan(
        db_session,
        profile.id,
        "EUR",
        True,
        [{"duration": "month_1", "amount_minor": 1000, "enabled": True}],
    )
    subscription = await subscriptions.create_subscription(
        db_session, buyer, profile.id, "month_1", "grace-start"
    )
    paid_period = await db_session.scalar(
        select(SubscriptionPeriod).where(SubscriptionPeriod.subscription_id == subscription.id)
    )
    assert paid_period
    initial_attempt = await db_session.get(PaymentAttempt, paid_period.payment_attempt_id)
    assert initial_attempt
    payload, signature = finance.development_webhook_payload(initial_attempt)
    await finance.process_development_webhook(db_session, payload, signature)
    subscription.current_period_end = datetime.now(UTC) - timedelta(seconds=1)
    assert await subscriptions.renew_due_subscriptions(db_session) == 1
    failed_period = await db_session.scalar(
        select(SubscriptionPeriod).where(
            SubscriptionPeriod.subscription_id == subscription.id,
            SubscriptionPeriod.sequence == 2,
        )
    )
    assert failed_period
    failed_attempt = await db_session.get(PaymentAttempt, failed_period.payment_attempt_id)
    assert failed_attempt
    failed_attempt.status = PaymentStatus.failed
    await subscriptions.fail_payment_attempt(db_session, failed_attempt)
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Grace access",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.subscription,
    )
    db_session.add(content)
    await db_session.flush()
    assert subscription.status is SubscriptionStatus.grace_period
    assert subscription.grace_period_end and await can_access_content(db_session, content, buyer)
    subscription.grace_period_end = datetime.now(UTC) - timedelta(seconds=1)
    assert await subscriptions.finalize_expired_subscriptions(db_session) == 1
    assert subscription.status is SubscriptionStatus.expired
    assert not await can_access_content(db_session, content, buyer)


@pytest.mark.asyncio
async def test_one_promotion_can_price_each_duration_independently(db_session):
    _owner, profile = await creator(db_session, "duration-promo-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "duration-promo-buyer@example.com", "strong-password-123", None
    )
    await subscriptions.configure_plan(
        db_session,
        profile.id,
        "EUR",
        True,
        [
            {"duration": "month_1", "amount_minor": 1000, "enabled": True},
            {"duration": "month_3", "amount_minor": 3000, "enabled": True},
            {"duration": "month_6", "amount_minor": 6000, "enabled": True},
            {"duration": "month_12", "amount_minor": 12000, "enabled": True},
        ],
    )
    now = datetime.now(UTC)
    promotion = SubscriptionPromotion(
        creator_id=profile.id,
        name="Every duration",
        eligibility=PromotionEligibility.all_eligible,
        renewal_scope=PromotionRenewalScope.initial_and_renewal,
        start_at=now - timedelta(minutes=1),
    )
    db_session.add(promotion)
    await db_session.flush()
    db_session.add_all(
        [
            SubscriptionPromotionRule(
                promotion_id=promotion.id, duration="month_1", discount_basis_points=2000
            ),
            SubscriptionPromotionRule(
                promotion_id=promotion.id, duration="month_3", discount_basis_points=2500
            ),
            SubscriptionPromotionRule(
                promotion_id=promotion.id, duration="month_6", discount_basis_points=3500
            ),
            SubscriptionPromotionRule(
                promotion_id=promotion.id, duration="month_12", discount_basis_points=4500
            ),
        ]
    )
    prices = {"month_1": 1000, "month_3": 3000, "month_6": 6000, "month_12": 12000}
    expected = {
        "month_1": (2000, 800),
        "month_3": (2500, 2250),
        "month_6": (3500, 3900),
        "month_12": (4500, 6600),
    }
    for duration, (expected_bps, charged) in expected.items():
        promotion_row, basis_points = await subscriptions._promotion(
            db_session, buyer, profile.id, duration, renewal=False
        )
        assert promotion_row and basis_points == expected_bps
        assert (
            prices[duration] - subscriptions.discount_amount(prices[duration], basis_points)
            == charged
        )


@pytest.mark.asyncio
async def test_promotion_scheduling_and_overlap_are_deterministic(db_session):
    _owner, profile = await creator(db_session, "overlap-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "overlap-buyer@example.com", "strong-password-123", None
    )
    now = datetime.now(UTC)
    valid = SubscriptionPromotion(
        creator_id=profile.id,
        name="all durations",
        eligibility=PromotionEligibility.all_eligible,
        renewal_scope=PromotionRenewalScope.initial_only,
        start_at=now - timedelta(minutes=1),
    )
    future = SubscriptionPromotion(
        creator_id=profile.id,
        name="future",
        eligibility=PromotionEligibility.all_eligible,
        renewal_scope=PromotionRenewalScope.initial_only,
        start_at=now + timedelta(minutes=1),
    )
    db_session.add_all([valid, future])
    await db_session.flush()
    db_session.add_all(
        [
            SubscriptionPromotionRule(
                promotion_id=valid.id, duration="month_1", discount_basis_points=3000
            ),
            SubscriptionPromotionRule(
                promotion_id=valid.id, duration="month_3", discount_basis_points=3000
            ),
            SubscriptionPromotionRule(
                promotion_id=future.id, duration="month_1", discount_basis_points=9000
            ),
        ]
    )
    selected, bps = await subscriptions._promotion(
        db_session, buyer, profile.id, "month_1", renewal=False
    )
    assert selected and selected.id == valid.id and bps == 3000
    selected, bps = await subscriptions._promotion(
        db_session, buyer, profile.id, "month_3", renewal=False
    )
    assert selected and selected.id == valid.id and bps == 3000


@pytest.mark.asyncio
async def test_period_price_and_promotion_snapshot_survive_later_edits(db_session):
    _owner, profile = await creator(db_session, "history-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "history-buyer@example.com", "strong-password-123", None
    )
    await subscriptions.configure_plan(
        db_session,
        profile.id,
        "EUR",
        True,
        [{"duration": "month_1", "amount_minor": 999, "enabled": True}],
    )
    now = datetime.now(UTC)
    promotion = SubscriptionPromotion(
        creator_id=profile.id,
        name="snapshot",
        eligibility=PromotionEligibility.all_eligible,
        renewal_scope=PromotionRenewalScope.initial_only,
        start_at=now - timedelta(minutes=1),
    )
    db_session.add(promotion)
    await db_session.flush()
    rule = SubscriptionPromotionRule(
        promotion_id=promotion.id, duration="month_1", discount_basis_points=2000
    )
    db_session.add(rule)
    subscription = await subscriptions.create_subscription(
        db_session, buyer, profile.id, "month_1", "snapshot-start"
    )
    period = await db_session.scalar(
        select(SubscriptionPeriod).where(SubscriptionPeriod.subscription_id == subscription.id)
    )
    assert period
    price = await db_session.scalar(
        select(SubscriptionPlanPrice).where(SubscriptionPlanPrice.plan_id == subscription.plan_id)
    )
    assert price
    price.amount_minor = 1999
    rule.discount_basis_points = 5000
    await db_session.flush()
    assert (
        period.base_amount_minor,
        period.discount_basis_points,
        period.charged_amount_minor,
    ) == (999, 2000, 800)
