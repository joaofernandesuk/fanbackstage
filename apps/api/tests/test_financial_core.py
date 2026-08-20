import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from app.accounts import service as accounts
from app.content.access import can_access_content
from app.core.config import Settings
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
from app.models.finance import (
    LedgerDirection,
    LedgerEntry,
    PaymentAttempt,
    PaymentStatus,
    PaymentWebhookEvent,
    PurchaseStatus,
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


@pytest.mark.asyncio
async def test_paid_ppv_is_idempotent_balanced_and_entitles_buyer(db_session):
    owner, profile = await approved_creator(db_session, "finance-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "finance-buyer@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="PPV",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=999,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    purchase = await finance.initiate_purchase(db_session, buyer, content.id, "same-request")
    assert (
        await finance.initiate_purchase(db_session, buyer, content.id, "same-request")
    ).id == purchase.id
    attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    settled = await finance.process_development_webhook(db_session, payload, signature)
    assert settled and settled.status is PurchaseStatus.paid
    await db_session.flush()
    assert await can_access_content(db_session, content, buyer)
    entries = (
        await db_session.scalars(
            select(LedgerEntry).where(LedgerEntry.transaction_id == settled.ledger_transaction_id)
        )
    ).all()
    debit = sum(entry.amount_minor for entry in entries if entry.direction is LedgerDirection.debit)
    credit = sum(
        entry.amount_minor for entry in entries if entry.direction is LedgerDirection.credit
    )
    assert debit == credit == 999
    assert settled.platform_fee_minor + settled.creator_amount_minor == settled.gross_amount_minor
    assert await finance.process_development_webhook(db_session, payload, signature) is None
    assert await db_session.scalar(select(func.count()).select_from(PaymentWebhookEvent)) == 1
    with pytest.raises(DBAPIError):
        await db_session.execute(
            update(LedgerEntry).where(LedgerEntry.id == entries[0].id).values(amount_minor=1)
        )
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_full_refund_reverses_value_and_revokes_entitlement(db_session):
    owner, profile = await approved_creator(db_session, "refund-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "refund-buyer@example.com", "strong-password-123", None
    )
    admin, _ = await accounts.register(
        db_session, "refund-admin@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.video,
        title="Refundable PPV",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=1000,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    purchase = await finance.initiate_purchase(db_session, buyer, content.id, "refund-request")
    attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    purchase = await finance.process_development_webhook(db_session, payload, signature)
    assert purchase
    refunded = await finance.refund_purchase(db_session, purchase, admin, "Customer support refund")
    await db_session.flush()
    assert refunded.status is PurchaseStatus.refunded
    assert not await can_access_content(db_session, content, buyer)
    entry_count = await db_session.scalar(select(func.count()).select_from(LedgerEntry))
    assert (
        await finance.refund_purchase(db_session, refunded, admin, "duplicate")
    ).id == refunded.id
    assert await db_session.scalar(select(func.count()).select_from(LedgerEntry)) == entry_count


def test_commission_uses_integer_minor_units_without_rounding_drift():
    assert finance.commission_amount(101, 2000) == (20, 81)
    assert finance.commission_amount(1, 9999) == (0, 1)


def test_payment_webhook_signature_is_required_and_verified():
    payload = b'{"id":"event","type":"payment.succeeded","payment_reference":"ref"}'
    with pytest.raises(finance.FinancialError, match="signature"):
        finance.verify_development_webhook(payload, "invalid")


def test_production_rejects_the_development_payment_provider():
    with pytest.raises(RuntimeError, match="development payment provider"):
        Settings(
            environment="production",
            payment_provider="development",
            session_secret="production-secret",
            kyc_provider="production",
        ).validate_production()


@pytest.mark.asyncio
async def test_reconciliation_settles_a_succeeded_attempt_without_duplicate_entries(db_session):
    owner, profile = await approved_creator(db_session, "reconcile-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "reconcile-buyer@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Recoverable PPV",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=500,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    purchase = await finance.initiate_purchase(db_session, buyer, content.id, "recover-settlement")
    attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    assert attempt
    attempt.status = PaymentStatus.succeeded
    assert await finance.reconcile_succeeded_payments(db_session) == 1
    assert purchase.status is PurchaseStatus.paid
    assert await finance.reconcile_succeeded_payments(db_session) == 0


@pytest.mark.asyncio
async def test_purchase_requires_published_approved_ppv_content(db_session):
    owner, profile = await approved_creator(db_session, "unpublished-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "unpublished-buyer@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Not yet published",
        access_policy=AccessPolicy.ppv,
        price_amount_minor=999,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    with pytest.raises(finance.FinancialError, match="not available"):
        await finance.initiate_purchase(db_session, buyer, content.id, "not-published")


@pytest.mark.asyncio
async def test_commission_snapshot_does_not_change_after_rule_update(db_session):
    owner, profile = await approved_creator(db_session, "commission-owner@example.com")
    buyer_one, _ = await accounts.register(
        db_session, "commission-one@example.com", "strong-password-123", None
    )
    buyer_two, _ = await accounts.register(
        db_session, "commission-two@example.com", "strong-password-123", None
    )
    first = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="First rate",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=999,
        price_currency="EUR",
    )
    second = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Second rate",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=999,
        price_currency="EUR",
    )
    db_session.add_all([first, second])
    await db_session.flush()
    initial = await finance.initiate_purchase(db_session, buyer_one, first.id, "rate-one")
    rule = await db_session.scalar(
        select(finance.CommissionRule).where(finance.CommissionRule.revenue_type == "ppv")
    )
    assert rule
    rule.basis_points = 2500
    later = await finance.initiate_purchase(db_session, buyer_two, second.id, "rate-two")
    assert initial.commission_basis_points == 2000
    assert initial.platform_fee_minor == 199
    assert later.commission_basis_points == 2500
    assert later.platform_fee_minor == 249


@pytest.mark.asyncio
async def test_creator_summary_excludes_refunded_sales_and_keeps_currency_isolated(db_session):
    owner, profile = await approved_creator(db_session, "summary-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "summary-buyer@example.com", "strong-password-123", None
    )
    admin, _ = await accounts.register(
        db_session, "summary-admin@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Summary",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=101,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    purchase = await finance.initiate_purchase(db_session, buyer, content.id, "summary")
    attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    settled = await finance.process_development_webhook(db_session, payload, signature)
    assert settled
    summary = await finance.creator_financial_summary(db_session, profile.id, "EUR")
    assert summary == {
        "pending_amount_minor": 81,
        "available_amount_minor": 0,
        "ppv_gross_amount_minor": 101,
        "platform_fee_amount_minor": 20,
        "creator_net_amount_minor": 81,
    }
    await finance.refund_purchase(db_session, settled, admin, "support")
    assert (await finance.creator_financial_summary(db_session, profile.id, "EUR"))[
        "ppv_gross_amount_minor"
    ] == 0
    assert await finance.creator_balances(db_session, profile.id, "USD") == {
        "pending_amount_minor": 0,
        "available_amount_minor": 0,
    }


@pytest.mark.asyncio
async def test_earnings_release_is_balanced_and_does_not_reuse_a_previous_balance_key(
    db_session, monkeypatch
):
    owner, profile = await approved_creator(db_session, "release-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "release-buyer@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Release",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=100,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    purchase = await finance.initiate_purchase(db_session, buyer, content.id, "release")
    attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    assert await finance.process_development_webhook(db_session, payload, signature)
    monkeypatch.setattr(
        finance, "get_settings", lambda: Settings(creator_earnings_settlement_seconds=0)
    )
    release = await finance.release_creator_earnings(db_session, profile.id, "EUR")
    assert release and release.transaction_type.value == "earnings_release"
    assert await finance.creator_balances(db_session, profile.id, "EUR") == {
        "pending_amount_minor": 0,
        "available_amount_minor": 80,
    }
    assert await finance.release_creator_earnings(db_session, profile.id, "EUR") is None
    second_buyer, _ = await accounts.register(
        db_session, "release-buyer-two@example.com", "strong-password-123", None
    )
    second = await finance.initiate_purchase(db_session, second_buyer, content.id, "release-two")
    second_attempt = await db_session.get(PaymentAttempt, second.payment_attempt_id)
    assert second_attempt
    second_payload, second_signature = finance.development_webhook_payload(second_attempt)
    assert await finance.process_development_webhook(db_session, second_payload, second_signature)
    second_release = await finance.release_creator_earnings(db_session, profile.id, "EUR")
    assert second_release and second_release.id != release.id
    assert await finance.creator_balances(db_session, profile.id, "EUR") == {
        "pending_amount_minor": 0,
        "available_amount_minor": 160,
    }
