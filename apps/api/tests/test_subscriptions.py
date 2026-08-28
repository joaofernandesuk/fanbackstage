import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest import trusted_self_attested_accounts as accounts
from fastapi import HTTPException
from sqlalchemy import func, select
from starlette.requests import Request
from surface_policy_helpers import publish_creator_identity_policy

from app.accounts import service as raw_accounts
from app.api.routes import subscriptions as subscription_routes
from app.content.access import can_access_content
from app.core.config import Settings, get_settings
from app.creators import service as creators
from app.db.session import SessionLocal
from app.finance import service as finance
from app.models.audit import AuditEvent
from app.models.content import (
    AccessPolicy,
    ContentEntitlement,
    ContentItem,
    ContentStatus,
    ContentType,
    EntitlementStatus,
    ModerationStatus,
)
from app.models.creator import CreatorStatus, CreatorVerification, VerificationStatus
from app.models.finance import (
    ExcessCaptureSource,
    LedgerEntry,
    LedgerTransaction,
    LedgerTransactionType,
    PaymentAttempt,
    PaymentRefundRequirement,
    PaymentStatus,
)
from app.models.identity import User
from app.models.messaging import UserBlock
from app.models.subscription import (
    PromotionEligibility,
    PromotionRenewalScope,
    Subscription,
    SubscriptionPeriod,
    SubscriptionPeriodStatus,
    SubscriptionPlanPrice,
    SubscriptionPromotion,
    SubscriptionPromotionRule,
    SubscriptionRenewalAttempt,
    SubscriptionStatus,
)
from app.schemas.subscription import SubscriptionStart
from app.subscriptions import service as subscriptions


async def creator(db, email: str):
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


def signed_payment_event(
    attempt: PaymentAttempt, event_type: str, event_id: str
) -> tuple[bytes, str]:
    payload = json.dumps(
        {
            "id": event_id,
            "type": event_type,
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(
        get_settings().payment_webhook_secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return payload, signature


@pytest.mark.asyncio
async def test_unattested_subscription_api_fails_safely_without_payment_attempt(db_session):
    _owner, profile = await creator(db_session, "unattested-sub-owner@example.com")
    buyer, _ = await raw_accounts.register(
        db_session,
        "unattested-sub-buyer@example.com",
        "strong-password-123",
        None,
        country_code="PT",
    )
    await subscriptions.configure_plan(
        db_session,
        profile.id,
        "EUR",
        True,
        [{"duration": "month_1", "amount_minor": 1_000, "enabled": True}],
    )

    with pytest.raises(HTTPException) as exc:
        await subscription_routes.start(
            profile.id,
            SubscriptionStart(duration="month_1"),
            Request({"type": "http", "client": ("127.0.0.1", 50000), "headers": []}),
            (buyer, None),
            db_session,
            "unattested-subscription",
        )
    assert exc.value.status_code == 403
    assert "Age verification is required" in str(exc.value.detail)
    assert await db_session.scalar(select(PaymentAttempt.id)) is None


@pytest.mark.asyncio
async def test_subscription_trusted_country_conflict_creates_no_subscription_or_attempt(
    db_session, monkeypatch
):
    _owner, profile = await creator(db_session, "sub-country-conflict-owner@example.com")
    buyer, _ = await accounts.register(
        db_session,
        "subscription-country-conflict-buyer@example.com",
        "strong-password-123",
        None,
    )
    buyer.country_code = "PT"
    await subscriptions.configure_plan(
        db_session,
        profile.id,
        "EUR",
        True,
        [{"duration": "month_1", "amount_minor": 1_000, "enabled": True}],
    )
    monkeypatch.setattr(
        "app.compliance.http.get_settings",
        lambda: Settings(
            environment="test",
            trusted_country_header="x-country",
            trusted_proxy_cidrs="127.0.0.1/32",
        ),
    )
    request = Request(
        {
            "type": "http",
            "client": ("127.0.0.1", 50000),
            "headers": [(b"x-country", b"GB")],
        }
    )

    with pytest.raises(HTTPException) as exc:
        await subscription_routes.start(
            profile.id,
            SubscriptionStart(duration="month_1"),
            request,
            (buyer, None),
            db_session,
            "subscription-country-conflict",
        )

    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "COUNTRY_SIGNAL_CONFLICT"
    assert await db_session.scalar(select(Subscription.id)) is None
    assert await db_session.scalar(select(PaymentAttempt.id)) is None


@pytest.mark.asyncio
async def test_concurrent_same_key_subscription_returns_one_canonical_command(
    db_session, monkeypatch
):
    _owner, profile = await creator(db_session, "sub-concurrent-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "sub-concurrent-buyer@example.com", "strong-password-123", None
    )
    await subscriptions.configure_plan(
        db_session,
        profile.id,
        "EUR",
        True,
        [{"duration": "month_1", "amount_minor": 1_000, "enabled": True}],
    )
    buyer_id, creator_id = buyer.id, profile.id
    await db_session.commit()

    first_locked = asyncio.Event()
    release_first = asyncio.Event()
    original_lock = finance.lock_payment_idempotency

    async def paused_lock(db, user_id, key):
        existing = await original_lock(db, user_id, key)
        if existing is None and not first_locked.is_set():
            first_locked.set()
            await asyncio.wait_for(release_first.wait(), timeout=5)
        return existing

    monkeypatch.setattr(subscriptions, "lock_payment_idempotency", paused_lock)

    async def initiate() -> str:
        async with SessionLocal() as session:
            session_buyer = await session.get(User, buyer_id)
            assert session_buyer
            subscription = await subscriptions.create_subscription(
                session,
                session_buyer,
                creator_id,
                "month_1",
                "concurrent-subscription-key",
            )
            await session.commit()
            return str(subscription.id)

    first = asyncio.create_task(initiate())
    await asyncio.wait_for(first_locked.wait(), timeout=5)
    second = asyncio.create_task(initiate())
    await asyncio.sleep(0)
    release_first.set()
    assert len(set(await asyncio.gather(first, second))) == 1
    async with SessionLocal() as verification:
        assert (
            await verification.scalar(
                select(func.count())
                .select_from(PaymentAttempt)
                .where(
                    PaymentAttempt.buyer_user_id == buyer_id,
                    PaymentAttempt.idempotency_key == "concurrent-subscription-key",
                )
            )
            == 1
        )
        assert (
            await verification.scalar(
                select(func.count())
                .select_from(Subscription)
                .where(
                    Subscription.subscriber_user_id == buyer_id,
                    Subscription.creator_id == creator_id,
                )
            )
            == 1
        )


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
    # A provider replay must neither add another webhook settlement nor duplicate
    # the immutable subscription-charge posting/entitlement.
    assert await finance.process_development_webhook(db_session, payload, signature) is None
    assert await subscriptions.settle_payment_attempt(db_session, attempt) is subscription
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(LedgerTransaction)
            .where(LedgerTransaction.idempotency_key == f"subscription-period:{period.id}")
        )
        == 1
    )
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
    renewal_attempt = await db_session.scalar(
        select(SubscriptionRenewalAttempt).where(
            SubscriptionRenewalAttempt.subscription_period_id == failed_period.id
        )
    )
    assert renewal_attempt
    renewal_attempt.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await subscriptions.retry_failed_subscription_renewals(db_session) == 1
    assert await subscriptions.retry_failed_subscription_renewals(db_session) == 0
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(SubscriptionRenewalAttempt)
            .where(SubscriptionRenewalAttempt.subscription_period_id == failed_period.id)
        )
        == 2
    )


@pytest.mark.asyncio
async def test_cancelled_subscription_does_not_retry_pending_renewal(db_session):
    _owner, profile = await creator(db_session, "retry-cancel-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "retry-cancel-buyer@example.com", "strong-password-123", None
    )
    await subscriptions.configure_plan(
        db_session,
        profile.id,
        "EUR",
        True,
        [{"duration": "month_1", "amount_minor": 1000, "enabled": True}],
    )
    subscription = await subscriptions.create_subscription(
        db_session, buyer, profile.id, "month_1", "retry-cancel"
    )
    paid = await db_session.scalar(
        select(SubscriptionPeriod).where(SubscriptionPeriod.subscription_id == subscription.id)
    )
    assert paid
    attempt = await db_session.get(PaymentAttempt, paid.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db_session, payload, signature)
    subscription.current_period_end = datetime.now(UTC) - timedelta(seconds=1)
    assert await subscriptions.renew_due_subscriptions(db_session) == 1
    renewal = await db_session.scalar(
        select(SubscriptionPeriod).where(
            SubscriptionPeriod.subscription_id == subscription.id, SubscriptionPeriod.sequence == 2
        )
    )
    assert renewal
    renewal_attempt = await db_session.get(PaymentAttempt, renewal.payment_attempt_id)
    assert renewal_attempt
    renewal_attempt.status = PaymentStatus.failed
    await subscriptions.fail_payment_attempt(db_session, renewal_attempt)
    await subscriptions.set_auto_renew(db_session, buyer, subscription.id, False)
    count = await db_session.scalar(select(func.count()).select_from(PaymentAttempt))
    assert await subscriptions.retry_failed_subscription_renewals(db_session) == 0
    assert await db_session.scalar(select(func.count()).select_from(PaymentAttempt)) == count


@pytest.mark.asyncio
async def test_renewal_retry_exhaustion_is_terminal_and_replay_safe(db_session, monkeypatch):
    monkeypatch.setattr(
        subscriptions,
        "get_settings",
        lambda: Settings(subscription_renewal_retry_limit=1, subscription_grace_period_days=0),
    )
    owner, profile = await creator(db_session, "retry-exhaust-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "retry-exhaust-buyer@example.com", "strong-password-123", None
    )
    await subscriptions.configure_plan(
        db_session,
        profile.id,
        "EUR",
        True,
        [{"duration": "month_1", "amount_minor": 1000, "enabled": True}],
    )
    subscription = await subscriptions.create_subscription(
        db_session, buyer, profile.id, "month_1", "retry-exhaust"
    )
    paid = await db_session.scalar(
        select(SubscriptionPeriod).where(SubscriptionPeriod.subscription_id == subscription.id)
    )
    assert paid
    first = await db_session.get(PaymentAttempt, paid.payment_attempt_id)
    assert first
    payload, signature = finance.development_webhook_payload(first)
    await finance.process_development_webhook(db_session, payload, signature)
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="retry exhaustion",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.subscription,
    )
    db_session.add(content)
    subscription.current_period_end = datetime.now(UTC) - timedelta(seconds=1)
    assert await subscriptions.renew_due_subscriptions(db_session) == 1
    renewal = await db_session.scalar(
        select(SubscriptionPeriod).where(
            SubscriptionPeriod.subscription_id == subscription.id, SubscriptionPeriod.sequence == 2
        )
    )
    assert renewal
    failed = await db_session.get(PaymentAttempt, renewal.payment_attempt_id)
    assert failed
    failed.status = PaymentStatus.failed
    await subscriptions.fail_payment_attempt(db_session, failed)
    attempt_count = await db_session.scalar(select(func.count()).select_from(PaymentAttempt))
    assert await subscriptions.retry_failed_subscription_renewals(db_session) == 0
    assert await subscriptions.retry_failed_subscription_renewals(db_session) == 0
    assert (
        await db_session.scalar(select(func.count()).select_from(PaymentAttempt)) == attempt_count
    )
    assert renewal.ledger_transaction_id is None and renewal.entitlement_id is None
    subscription.grace_period_end = datetime.now(UTC) - timedelta(seconds=1)
    assert await subscriptions.finalize_expired_subscriptions(db_session) == 1
    assert subscription.status is SubscriptionStatus.expired
    assert not await can_access_content(db_session, content, buyer)


@pytest.mark.asyncio
async def test_subscription_period_refund_reverses_once_and_only_revokes_the_selected_period(
    db_session,
):
    owner, profile = await creator(db_session, "subscription-refund-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "subscription-refund-buyer@example.com", "strong-password-123", None
    )
    admin, _ = await accounts.register(
        db_session, "subscription-refund-admin@example.com", "strong-password-123", None
    )
    await subscriptions.configure_plan(
        db_session,
        profile.id,
        "EUR",
        True,
        [{"duration": "month_1", "amount_minor": 1000, "enabled": True}],
    )
    subscription = await subscriptions.create_subscription(
        db_session, buyer, profile.id, "month_1", "subscription-refund-start"
    )
    first = await db_session.scalar(
        select(SubscriptionPeriod).where(SubscriptionPeriod.subscription_id == subscription.id)
    )
    assert first
    first_attempt = await db_session.get(PaymentAttempt, first.payment_attempt_id)
    assert first_attempt
    payload, signature = finance.development_webhook_payload(first_attempt)
    await finance.process_development_webhook(db_session, payload, signature)
    subscription.current_period_end = datetime.now(UTC) - timedelta(seconds=1)
    assert await subscriptions.renew_due_subscriptions(db_session) == 1
    second = await db_session.scalar(
        select(SubscriptionPeriod).where(
            SubscriptionPeriod.subscription_id == subscription.id,
            SubscriptionPeriod.sequence == 2,
        )
    )
    assert second
    second_attempt = await db_session.get(PaymentAttempt, second.payment_attempt_id)
    assert second_attempt
    payload, signature = finance.development_webhook_payload(second_attempt)
    await finance.process_development_webhook(db_session, payload, signature)
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Subscription refund access",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.subscription,
    )
    db_session.add(content)
    await db_session.flush()
    assert await can_access_content(db_session, content, buyer)

    original_charge = first.ledger_transaction_id
    assert original_charge
    await finance.refund_subscription_period(db_session, first, admin, "Historical correction")
    await db_session.flush()
    assert first.status.value == "refunded"
    assert first.ledger_transaction_id == original_charge
    assert subscription.status is SubscriptionStatus.active
    assert subscription.auto_renew and await can_access_content(db_session, content, buyer)

    await finance.refund_subscription_period(db_session, second, admin, "Current-period refund")
    await db_session.flush()
    assert second.status.value == "refunded"
    assert subscription.status is SubscriptionStatus.expired
    assert not subscription.auto_renew
    assert not await can_access_content(db_session, content, buyer)
    assert await finance.creator_balances(db_session, profile.id, "EUR") == {
        "pending_amount_minor": 0,
        "available_amount_minor": 0,
    }
    reversal_count = await db_session.scalar(
        select(func.count())
        .select_from(LedgerTransaction)
        .where(LedgerTransaction.idempotency_key == f"subscription-refund:{second.id}")
    )
    entry_count = await db_session.scalar(select(func.count()).select_from(LedgerEntry))
    assert (
        await finance.refund_subscription_period(db_session, second, admin, "duplicate") is second
    )
    assert await db_session.scalar(select(func.count()).select_from(LedgerEntry)) == entry_count
    assert reversal_count == 1
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.event_type == "subscription.period_refunded",
                AuditEvent.actor_user_id == admin.id,
                AuditEvent.target_id == str(second.id),
            )
        )
        == 1
    )


@pytest.mark.asyncio
async def test_signed_subscription_refund_reverses_exact_charge_and_revokes_access(db_session):
    _owner, profile = await creator(db_session, "signed-sub-refund-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "signed-sub-refund-buyer@example.com", "strong-password-123", None
    )
    await subscriptions.configure_plan(
        db_session,
        profile.id,
        "EUR",
        True,
        [{"duration": "month_1", "amount_minor": 1_100, "enabled": True}],
    )
    subscription = await subscriptions.create_subscription(
        db_session, buyer, profile.id, "month_1", "signed-sub-refund"
    )
    period = await db_session.scalar(
        select(SubscriptionPeriod).where(SubscriptionPeriod.subscription_id == subscription.id)
    )
    assert period
    attempt = await db_session.get(PaymentAttempt, period.payment_attempt_id)
    assert attempt
    success_payload, success_signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db_session, success_payload, success_signature)
    original_id = period.ledger_transaction_id
    assert original_id
    original_entries = (
        await db_session.scalars(
            select(LedgerEntry).where(LedgerEntry.transaction_id == original_id)
        )
    ).all()

    refund_payload = json.dumps(
        {
            "id": f"signed-sub-refund-{attempt.id}",
            "type": "payment.refunded",
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    refund_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(),
        refund_payload,
        hashlib.sha256,
    ).hexdigest()
    await finance.process_development_webhook(db_session, refund_payload, refund_signature)
    assert period.status.value == "refunded"
    assert subscription.status is SubscriptionStatus.expired
    assert attempt.status is PaymentStatus.refunded
    reversal = await db_session.scalar(
        select(LedgerTransaction).where(
            LedgerTransaction.reversal_of_transaction_id == original_id,
            LedgerTransaction.transaction_type == LedgerTransactionType.refund,
        )
    )
    assert reversal
    reversed_entries = (
        await db_session.scalars(
            select(LedgerEntry).where(LedgerEntry.transaction_id == reversal.id)
        )
    ).all()
    assert sorted(
        (entry.ledger_account_id, entry.direction.value, entry.amount_minor)
        for entry in reversed_entries
    ) == sorted(
        (
            entry.ledger_account_id,
            "credit" if entry.direction.value == "debit" else "debit",
            entry.amount_minor,
        )
        for entry in original_entries
    )


@pytest.mark.asyncio
async def test_signed_subscription_dispute_is_fail_closed_and_reversal_order_is_monotonic(
    db_session,
):
    _owner, profile = await creator(db_session, "signed-sub-dispute-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "signed-sub-dispute-buyer@example.com", "strong-password-123", None
    )
    await subscriptions.configure_plan(
        db_session,
        profile.id,
        "EUR",
        True,
        [{"duration": "month_1", "amount_minor": 1_100, "enabled": True}],
    )
    subscription = await subscriptions.create_subscription(
        db_session, buyer, profile.id, "month_1", "signed-sub-dispute"
    )
    period = await db_session.scalar(
        select(SubscriptionPeriod).where(SubscriptionPeriod.subscription_id == subscription.id)
    )
    assert period
    attempt = await db_session.get(PaymentAttempt, period.payment_attempt_id)
    assert attempt
    success_payload, success_signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db_session, success_payload, success_signature)
    assert period.status is SubscriptionPeriodStatus.active
    entitlement = await db_session.get(ContentEntitlement, period.entitlement_id)
    assert entitlement and entitlement.status is EntitlementStatus.active
    original_ledger_id = period.ledger_transaction_id
    assert original_ledger_id

    dispute_payload, dispute_signature = signed_payment_event(
        attempt, "payment.disputed", f"signed-sub-dispute-{attempt.id}"
    )
    await finance.process_development_webhook(db_session, dispute_payload, dispute_signature)
    assert attempt.status is PaymentStatus.disputed
    assert period.status is SubscriptionPeriodStatus.disputed
    assert subscription.status is SubscriptionStatus.suspended
    assert entitlement.status is EntitlementStatus.revoked
    assert (
        await db_session.scalar(
            select(LedgerTransaction.id).where(
                LedgerTransaction.reversal_of_transaction_id == original_ledger_id
            )
        )
        is None
    )

    late_success, late_success_signature = signed_payment_event(
        attempt, "payment.succeeded", f"signed-sub-late-success-{attempt.id}"
    )
    await finance.process_development_webhook(db_session, late_success, late_success_signature)
    assert attempt.status is PaymentStatus.disputed
    assert period.status is SubscriptionPeriodStatus.disputed

    refund_payload, refund_signature = signed_payment_event(
        attempt, "payment.refunded", f"signed-sub-after-dispute-refund-{attempt.id}"
    )
    await finance.process_development_webhook(db_session, refund_payload, refund_signature)
    assert attempt.status is PaymentStatus.refunded
    assert period.status is SubscriptionPeriodStatus.refunded
    chargeback_payload, chargeback_signature = signed_payment_event(
        attempt, "payment.chargeback", f"signed-sub-after-refund-chargeback-{attempt.id}"
    )
    await finance.process_development_webhook(db_session, chargeback_payload, chargeback_signature)
    assert attempt.status is PaymentStatus.chargeback
    assert period.status is SubscriptionPeriodStatus.chargeback
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(LedgerTransaction)
            .where(LedgerTransaction.reversal_of_transaction_id == original_ledger_id)
        )
        == 1
    )

    pending_buyer, _ = await accounts.register(
        db_session,
        "signed-sub-pending-dispute-buyer@example.com",
        "strong-password-123",
        None,
    )
    pending_subscription = await subscriptions.create_subscription(
        db_session,
        pending_buyer,
        profile.id,
        "month_1",
        "signed-sub-pending-dispute",
    )
    pending_period = await db_session.scalar(
        select(SubscriptionPeriod).where(
            SubscriptionPeriod.subscription_id == pending_subscription.id
        )
    )
    assert pending_period
    pending_attempt = await db_session.get(PaymentAttempt, pending_period.payment_attempt_id)
    assert pending_attempt
    pending_dispute, pending_dispute_signature = signed_payment_event(
        pending_attempt,
        "payment.disputed",
        f"pending-sub-dispute-{pending_attempt.id}",
    )
    await finance.process_development_webhook(
        db_session, pending_dispute, pending_dispute_signature
    )
    pending_success, pending_success_signature = signed_payment_event(
        pending_attempt,
        "payment.succeeded",
        f"pending-sub-late-success-{pending_attempt.id}",
    )
    await finance.process_development_webhook(
        db_session, pending_success, pending_success_signature
    )
    assert pending_attempt.status is PaymentStatus.disputed
    assert pending_period.status is SubscriptionPeriodStatus.disputed
    assert pending_period.ledger_transaction_id is None
    assert pending_period.entitlement_id is None


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


@pytest.mark.asyncio
async def test_retry_success_is_single_settlement_and_reconciliation_is_replay_safe(db_session):
    _owner, profile = await creator(db_session, "retry-success-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "retry-success-buyer@example.com", "strong-password-123", None
    )
    await subscriptions.configure_plan(
        db_session,
        profile.id,
        "EUR",
        True,
        [{"duration": "month_1", "amount_minor": 1000, "enabled": True}],
    )
    subscription = await subscriptions.create_subscription(
        db_session, buyer, profile.id, "month_1", "retry-success-initial"
    )
    initial = await db_session.scalar(
        select(SubscriptionPeriod).where(SubscriptionPeriod.subscription_id == subscription.id)
    )
    assert initial
    attempt = await db_session.get(PaymentAttempt, initial.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db_session, payload, signature)
    subscription.current_period_end = datetime.now(UTC) - timedelta(seconds=1)
    assert await subscriptions.renew_due_subscriptions(db_session) == 1
    renewal = await db_session.scalar(
        select(SubscriptionPeriod).where(
            SubscriptionPeriod.subscription_id == subscription.id, SubscriptionPeriod.sequence == 2
        )
    )
    assert renewal
    first = await db_session.get(PaymentAttempt, renewal.payment_attempt_id)
    assert first
    first.status = PaymentStatus.failed
    await subscriptions.fail_payment_attempt(db_session, first)
    record = await db_session.scalar(
        select(SubscriptionRenewalAttempt).where(
            SubscriptionRenewalAttempt.subscription_period_id == renewal.id
        )
    )
    assert record
    record.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await subscriptions.retry_failed_subscription_renewals(db_session) == 1
    assert await subscriptions.retry_failed_subscription_renewals(db_session) == 0
    retry = await db_session.scalar(
        select(PaymentAttempt)
        .join(SubscriptionRenewalAttempt)
        .where(
            SubscriptionRenewalAttempt.subscription_period_id == renewal.id,
            SubscriptionRenewalAttempt.attempt_number == 2,
        )
    )
    assert retry and retry.id != first.id
    retry.status = PaymentStatus.succeeded
    retry.completed_at = datetime.now(UTC)
    assert await finance.reconcile_succeeded_payments(db_session) == 1
    assert renewal.status.value == "active" and subscription.status is SubscriptionStatus.active
    assert await finance.reconcile_succeeded_payments(db_session) == 0
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(SubscriptionPeriod)
            .where(SubscriptionPeriod.subscription_id == subscription.id)
        )
        == 2
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(LedgerTransaction)
            .where(LedgerTransaction.idempotency_key == f"subscription-period:{renewal.id}")
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(SubscriptionRenewalAttempt)
            .where(SubscriptionRenewalAttempt.subscription_period_id == renewal.id)
        )
        == 2
    )


@pytest.mark.asyncio
async def test_failed_initial_subscription_retries_same_snapshot_and_settles_once(db_session):
    _owner, profile = await creator(db_session, "initial-retry-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "initial-retry-buyer@example.com", "strong-password-123", None
    )
    await subscriptions.configure_plan(
        db_session,
        profile.id,
        "EUR",
        True,
        [{"duration": "month_1", "amount_minor": 1_250, "enabled": True}],
    )
    subscription = await subscriptions.create_subscription(
        db_session, buyer, profile.id, "month_1", "initial-first"
    )
    period = await db_session.scalar(
        select(SubscriptionPeriod).where(SubscriptionPeriod.subscription_id == subscription.id)
    )
    assert period
    snapshot = (
        period.base_amount_minor,
        period.discount_amount_minor,
        period.charged_amount_minor,
        period.commission_basis_points,
    )
    first = await db_session.get(PaymentAttempt, period.payment_attempt_id)
    assert first
    first.status = PaymentStatus.failed
    assert await subscriptions.fail_payment_attempt(db_session, first) is subscription
    assert subscription.status is SubscriptionStatus.payment_failed

    replay = await subscriptions.create_subscription(
        db_session, buyer, profile.id, "month_1", "initial-first"
    )
    assert replay.id == subscription.id and period.payment_attempt_id == first.id
    retried = await subscriptions.create_subscription(
        db_session, buyer, profile.id, "month_1", "initial-second"
    )
    assert retried.id == subscription.id and period.payment_attempt_id != first.id
    assert snapshot == (
        period.base_amount_minor,
        period.discount_amount_minor,
        period.charged_amount_minor,
        period.commission_basis_points,
    )
    second = await db_session.get(PaymentAttempt, period.payment_attempt_id)
    assert second and second.amount_minor == period.charged_amount_minor

    # A late provider success for the rotated attempt is a real excess capture:
    # it is frozen as a refund liability and cannot mutate the current period.
    first.status = PaymentStatus.succeeded
    first.completed_at = datetime.now(UTC)
    assert await subscriptions.settle_payment_attempt(db_session, first) is None
    assert period.status.value == "pending" and period.payment_attempt_id == second.id
    refund_required = await db_session.scalar(
        select(PaymentRefundRequirement).where(
            PaymentRefundRequirement.payment_attempt_id == first.id
        )
    )
    assert refund_required
    assert (
        refund_required.source_type,
        refund_required.source_reference,
        refund_required.amount_minor,
        refund_required.status.value,
    ) == (
        ExcessCaptureSource.subscription_period,
        str(period.id),
        first.amount_minor,
        "required",
    )
    liability = await db_session.get(
        LedgerTransaction, refund_required.liability_ledger_transaction_id
    )
    assert liability
    assert liability.transaction_type is LedgerTransactionType.excess_capture_liability
    # Replaying the stale success remains idempotent.
    assert await subscriptions.settle_payment_attempt(db_session, first) is None
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(PaymentRefundRequirement)
            .where(PaymentRefundRequirement.payment_attempt_id == first.id)
        )
        == 1
    )

    second.status = PaymentStatus.succeeded
    second.completed_at = datetime.now(UTC)
    assert await subscriptions.settle_payment_attempt(db_session, second) is subscription
    assert subscription.status is SubscriptionStatus.active
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(SubscriptionRenewalAttempt)
            .where(SubscriptionRenewalAttempt.subscription_period_id == period.id)
        )
        == 2
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(LedgerTransaction)
            .where(LedgerTransaction.idempotency_key == f"subscription-period:{period.id}")
        )
        == 1
    )


@pytest.mark.parametrize("containment", ["kyc", "status", "block"])
@pytest.mark.asyncio
async def test_due_renewal_suppresses_charge_when_creator_access_is_contained(
    db_session, containment
):
    owner, profile = await creator(db_session, f"renew-contained-{containment}@example.com")
    buyer, _ = await accounts.register(
        db_session,
        f"renew-contained-buyer-{containment}@example.com",
        "strong-password-123",
        None,
    )
    await subscriptions.configure_plan(
        db_session,
        profile.id,
        "EUR",
        True,
        [{"duration": "month_1", "amount_minor": 1_000, "enabled": True}],
    )
    subscription = await subscriptions.create_subscription(
        db_session, buyer, profile.id, "month_1", f"contained-start-{containment}"
    )
    period = await db_session.scalar(
        select(SubscriptionPeriod).where(SubscriptionPeriod.subscription_id == subscription.id)
    )
    assert period
    attempt = await db_session.get(PaymentAttempt, period.payment_attempt_id)
    assert attempt
    attempt.status = PaymentStatus.succeeded
    attempt.completed_at = datetime.now(UTC)
    await subscriptions.settle_payment_attempt(db_session, attempt)
    subscription.current_period_end = datetime.now(UTC) - timedelta(seconds=1)
    if containment == "kyc":
        await publish_creator_identity_policy(db_session)
        db_session.add(
            CreatorVerification(
                creator_profile_id=profile.id,
                provider="development",
                provider_reference="later-failed-kyc",
                status=VerificationStatus.failed,
                adult_verified=False,
                created_at=datetime.now(UTC) + timedelta(seconds=1),
            )
        )
    elif containment == "status":
        profile.status = CreatorStatus.suspended
        profile.is_public = False
    else:
        db_session.add(UserBlock(blocker_user_id=owner.id, blocked_user_id=buyer.id))
    await db_session.flush()
    attempts_before = await db_session.scalar(select(func.count()).select_from(PaymentAttempt))

    assert await subscriptions.renew_due_subscriptions(db_session) == 0
    assert (
        await db_session.scalar(select(func.count()).select_from(PaymentAttempt)) == attempts_before
    )
    assert not subscription.auto_renew and subscription.cancel_at_period_end
    assert await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == "subscription.renewal_suppressed",
            AuditEvent.target_id == str(subscription.id),
        )
    )


@pytest.mark.asyncio
async def test_failed_renewal_retry_does_not_charge_after_relationship_block(db_session):
    owner, profile = await creator(db_session, "retry-contained-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "retry-contained-buyer@example.com", "strong-password-123", None
    )
    await subscriptions.configure_plan(
        db_session,
        profile.id,
        "EUR",
        True,
        [{"duration": "month_1", "amount_minor": 1_000, "enabled": True}],
    )
    subscription = await subscriptions.create_subscription(
        db_session, buyer, profile.id, "month_1", "retry-contained-start"
    )
    initial = await db_session.scalar(
        select(SubscriptionPeriod).where(SubscriptionPeriod.subscription_id == subscription.id)
    )
    assert initial
    initial_attempt = await db_session.get(PaymentAttempt, initial.payment_attempt_id)
    assert initial_attempt
    initial_attempt.status = PaymentStatus.succeeded
    initial_attempt.completed_at = datetime.now(UTC)
    await subscriptions.settle_payment_attempt(db_session, initial_attempt)
    subscription.current_period_end = datetime.now(UTC) - timedelta(seconds=1)
    assert await subscriptions.renew_due_subscriptions(db_session) == 1
    renewal = await db_session.scalar(
        select(SubscriptionPeriod).where(
            SubscriptionPeriod.subscription_id == subscription.id,
            SubscriptionPeriod.sequence == 2,
        )
    )
    assert renewal
    failed = await db_session.get(PaymentAttempt, renewal.payment_attempt_id)
    assert failed
    failed.status = PaymentStatus.failed
    await subscriptions.fail_payment_attempt(db_session, failed)
    record = await db_session.scalar(
        select(SubscriptionRenewalAttempt).where(
            SubscriptionRenewalAttempt.subscription_period_id == renewal.id,
            SubscriptionRenewalAttempt.payment_attempt_id == failed.id,
        )
    )
    assert record
    record.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
    db_session.add(UserBlock(blocker_user_id=owner.id, blocked_user_id=buyer.id))
    await db_session.flush()
    attempts_before = await db_session.scalar(select(func.count()).select_from(PaymentAttempt))

    assert await subscriptions.retry_failed_subscription_renewals(db_session) == 0
    assert (
        await db_session.scalar(select(func.count()).select_from(PaymentAttempt)) == attempts_before
    )
    assert not subscription.auto_renew and subscription.cancel_at_period_end
