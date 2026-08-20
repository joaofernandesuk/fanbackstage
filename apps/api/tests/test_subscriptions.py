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
from app.models.finance import PaymentAttempt
from app.models.subscription import (
    PromotionEligibility,
    PromotionRenewalScope,
    SubscriptionPeriod,
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
