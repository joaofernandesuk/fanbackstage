import pytest
from sqlalchemy import func, select

from app.accounts import service as accounts
from app.creators import service as creators
from app.finance import service as finance
from app.messaging import service as messaging
from app.models.creator import CreatorStatus
from app.models.finance import LedgerEntry, PaymentAttempt
from app.models.messaging import Message, MessagingPermission


async def creator(db, email):
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
async def test_paid_send_settles_once_and_only_delivers_after_payment(db_session):
    _owner, profile = await creator(db_session, "message-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "message-buyer@example.com", "strong-password-123", None
    )
    settings = await messaging.settings_for_creator(db_session, profile.id)
    settings.permission = MessagingPermission.anyone
    settings.send_fee_minor, settings.send_fee_currency = 250, "EUR"
    pending = await messaging.initiate_paid_send(
        db_session, buyer, profile.id, "hello", "message-send-1"
    )
    assert await db_session.scalar(select(func.count()).select_from(Message)) == 0
    assert (
        await messaging.initiate_paid_send(
            db_session, buyer, profile.id, "tampered", "message-send-1"
        )
    ).id == pending.id
    attempt = await db_session.get(PaymentAttempt, pending.payment_attempt_id)
    payload, signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db_session, payload, signature)
    await db_session.flush()
    assert pending.status == "paid" and pending.message_id
    assert await db_session.scalar(select(func.count()).select_from(Message)) == 1
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(LedgerEntry)
            .where(LedgerEntry.transaction_id == pending.ledger_transaction_id)
        )
        == 3
    )
    assert await finance.process_development_webhook(db_session, payload, signature) is None
    assert await db_session.scalar(select(func.count()).select_from(Message)) == 1
    await finance.refund_message_charge(db_session, pending, buyer, "support refund")
    await db_session.flush()
    assert pending.status == "refunded"
    assert await db_session.scalar(select(func.count()).select_from(Message)) == 1
