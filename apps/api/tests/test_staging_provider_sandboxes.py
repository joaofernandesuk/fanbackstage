import pytest
from sqlalchemy import func, select

from app.core.config import Settings
from app.finance import providers
from app.finance import service as finance
from app.models.finance import PaymentAttempt, PaymentStatus, PaymentWebhookEvent
from app.models.identity import User


@pytest.fixture
def staging_sandbox_settings(monkeypatch):
    settings = Settings(
        environment="test",
        payment_provider="staging_sandbox",
        staging_payment_webhook_secret="staging-payment-webhook-secret-with-32-characters",
    )
    monkeypatch.setattr(providers, "get_settings", lambda: settings)
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
