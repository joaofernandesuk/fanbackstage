from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.accounts import service as accounts
from app.creators import service as creators
from app.finance import service as finance
from app.messaging import service as messaging
from app.models.content import (
    AccessPolicy,
    ContentEntitlement,
    ContentItem,
    ContentStatus,
    ContentType,
    ModerationStatus,
)
from app.models.creator import CreatorStatus
from app.models.finance import LedgerEntry, PaymentAttempt
from app.models.messaging import ConversationParticipant, Message, MessagingPermission, UserBlock
from app.models.social import Follow


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
    settings.send_fee_minor = 900
    assert pending.gross_amount_minor == 250
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


@pytest.mark.asyncio
async def test_marking_one_conversation_read_does_not_mark_another(db_session):
    owner_a, creator_a = await creator(db_session, "read-owner-a@example.com")
    owner_b, creator_b = await creator(db_session, "read-owner-b@example.com")
    viewer, _ = await accounts.register(
        db_session, "read-viewer@example.com", "strong-password-123", None
    )
    first = await messaging.send_message(db_session, viewer, creator_a.id, "first")
    second = await messaging.send_message(db_session, viewer, creator_b.id, "second")
    await messaging.send_in_conversation(db_session, owner_a, first.conversation_id, "reply")
    await messaging.send_in_conversation(db_session, owner_b, second.conversation_id, "reply")
    await messaging.mark_read(db_session, viewer, first.conversation_id)
    first_state = await db_session.scalar(
        select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == first.conversation_id,
            ConversationParticipant.user_id == viewer.id,
        )
    )
    second_state = await db_session.scalar(
        select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == second.conversation_id,
            ConversationParticipant.user_id == viewer.id,
        )
    )
    assert first_state and first_state.last_read_at
    assert second_state and second_state.last_read_at is None


@pytest.mark.asyncio
async def test_creator_messaging_eligibility_and_block_override(db_session):
    _owner, profile = await creator(db_session, "eligibility-owner@example.com")
    viewer, _ = await accounts.register(
        db_session, "eligibility-viewer@example.com", "strong-password-123", None
    )
    settings = await messaging.settings_for_creator(db_session, profile.id)
    settings.permission = MessagingPermission.anyone
    assert await messaging.can_message(db_session, viewer, profile)
    settings.permission = MessagingPermission.nobody
    assert not await messaging.can_message(db_session, viewer, profile)
    settings.permission = MessagingPermission.followers
    db_session.add(Follow(user_id=viewer.id, creator_id=profile.id))
    await db_session.flush()
    assert await messaging.can_message(db_session, viewer, profile)
    settings.permission = MessagingPermission.subscribers
    db_session.add(
        ContentEntitlement(
            subject_user_id=viewer.id,
            creator_id=profile.id,
            source_type="test",
            source_reference="test",
            valid_from=datetime.now(UTC),
        )
    )
    await db_session.flush()
    assert await messaging.can_message(db_session, viewer, profile)
    db_session.add(UserBlock(blocker_user_id=profile.user_id, blocked_user_id=viewer.id))
    await db_session.flush()
    assert not await messaging.can_message(db_session, viewer, profile)


@pytest.mark.asyncio
async def test_previous_customer_requires_settled_purchase_and_respects_block(db_session):
    owner, profile = await creator(db_session, "customer-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "customer-buyer@example.com", "strong-password-123", None
    )
    stranger, _ = await accounts.register(
        db_session, "customer-stranger@example.com", "strong-password-123", None
    )
    settings = await messaging.settings_for_creator(db_session, profile.id)
    settings.permission = MessagingPermission.previous_customers
    assert not await messaging.can_message(db_session, buyer, profile)
    assert not await messaging.can_message(db_session, stranger, profile)
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="PPV",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=500,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    purchase = await finance.initiate_purchase(db_session, buyer, content.id, "previous-customer")
    attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    payload, signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db_session, payload, signature)
    assert await messaging.can_message(db_session, buyer, profile)
    db_session.add(UserBlock(blocker_user_id=profile.user_id, blocked_user_id=buyer.id))
    await db_session.flush()
    assert not await messaging.can_message(db_session, buyer, profile)
