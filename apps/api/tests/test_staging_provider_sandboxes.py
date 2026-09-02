import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.creators import service as creators
from app.finance import providers
from app.finance import service as finance
from app.models.creator import CreatorProfile, CreatorStatus, VerificationStatus
from app.models.finance import PaymentAttempt, PaymentStatus, PaymentWebhookEvent
from app.models.identity import User


@pytest.fixture
def staging_sandbox_settings(monkeypatch):
    settings = Settings(
        environment="test",
        payment_provider="staging_sandbox",
        kyc_provider="staging_sandbox",
        staging_payment_webhook_secret="staging-payment-webhook-secret-with-32-characters",
        staging_kyc_webhook_secret="staging-kyc-webhook-secret-with-32-characters",
    )
    monkeypatch.setattr(providers, "get_settings", lambda: settings)
    monkeypatch.setattr(creators, "get_settings", lambda: settings)
    return settings


@pytest.mark.asyncio
async def test_staging_payment_is_signed_async_and_replay_safe(
    db_session, staging_sandbox_settings
):
    buyer = User(email="sandbox-payment@example.com", password_hash="x", country_code="PT")
    db_session.add(buyer)
    await db_session.flush()
    attempt = PaymentAttempt(
        buyer_user_id=buyer.id,
        provider="staging_sandbox",
        provider_reference="stgpay_test_async",
        amount_minor=100,
        currency="EUR",
        idempotency_key="sandbox-payment-test",
    )
    db_session.add(attempt)
    await db_session.flush()
    queued = await finance.staging_checkout(db_session, attempt, outcome="SUCCESS")
    assert attempt.status is PaymentStatus.pending
    assert (await finance.staging_checkout(db_session, attempt, outcome="SUCCESS")).id == queued.id
    assert await finance.deliver_due_staging_payment_events(db_session) == 1
    assert attempt.status is PaymentStatus.succeeded
    assert await finance.deliver_due_staging_payment_events(db_session) == 0
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(PaymentWebhookEvent)
            .where(PaymentWebhookEvent.provider == "staging_sandbox")
        )
    ) == 1


@pytest.mark.asyncio
async def test_staging_payment_rejects_invalid_signature(db_session, staging_sandbox_settings):
    with pytest.raises(finance.FinancialError, match="signature"):
        await finance.process_payment_webhook(
            db_session,
            "staging_sandbox",
            b'{"id":"event","type":"payment.succeeded","payment_reference":"unknown"}',
            "bad",
        )


@pytest.mark.asyncio
async def test_staging_kyc_is_signed_async_and_replay_safe(db_session, staging_sandbox_settings):
    user = User(email="sandbox-kyc@example.com", password_hash="x", country_code="PT")
    db_session.add(user)
    await db_session.flush()
    profile = CreatorProfile(
        user_id=user.id,
        username="sandbox-kyc",
        display_name="Sandbox KYC",
        status=CreatorStatus.pending_verification,
    )
    db_session.add(profile)
    await db_session.flush()
    verification = await creators.start_staging_kyc(db_session, profile, user.id)
    queued = await creators.queue_staging_kyc_outcome(db_session, verification, "VERIFIED")
    assert queued.delivered_at is None
    assert await creators.deliver_due_staging_kyc_events(db_session) == 1
    assert verification.status is VerificationStatus.verified
    assert verification.identity_verified and verification.adult_verified
    assert profile.status is CreatorStatus.pending_review
    assert await creators.deliver_due_staging_kyc_events(db_session) == 0


@pytest.mark.asyncio
async def test_staging_kyc_rejects_invalid_signature(db_session, staging_sandbox_settings):
    with pytest.raises(ValueError, match="signature"):
        await creators.process_staging_kyc_webhook(
            db_session,
            b'{"id":"event","type":"kyc.verified","provider_reference":"unknown"}',
            "bad",
        )
