import pytest
from sqlalchemy import select

from app.accounts.service import register
from app.models.notification import (
    DeliveryStatus,
    InAppNotification,
    NotificationChannel,
    NotificationClass,
    NotificationDeliveryAttempt,
    NotificationPriority,
)
from app.notifications import service


@pytest.mark.asyncio
async def test_intent_dedupes_and_in_app_is_recipient_scoped(db_session):
    first, _ = await register(db_session, "first@example.com", "strong-password-123", None)
    second, _ = await register(db_session, "second@example.com", "strong-password-123", None)
    first_intent = await service.create_intent(
        db_session,
        recipient_user_id=first.id,
        notification_type="SUBSCRIPTION_STARTED",
        classification=NotificationClass.transactional,
        source_domain="subscriptions",
        source_id="period-1",
        payload={
            "title": "Subscription started",
            "body": "Welcome",
            "target_path": "/subscriptions",
        },
        channels=(NotificationChannel.in_app,),
    )
    duplicate = await service.create_intent(
        db_session,
        recipient_user_id=first.id,
        notification_type="SUBSCRIPTION_STARTED",
        classification=NotificationClass.transactional,
        source_domain="subscriptions",
        source_id="period-1",
        payload={"title": "Ignored", "body": "Ignored"},
        channels=(NotificationChannel.in_app,),
    )
    await db_session.commit()
    assert duplicate.id == first_intent.id
    assert (
        len(
            (
                await db_session.scalars(
                    select(InAppNotification).where(InAppNotification.recipient_user_id == first.id)
                )
            ).all()
        )
        == 1
    )
    assert not (
        await db_session.scalars(
            select(InAppNotification).where(InAppNotification.recipient_user_id == second.id)
        )
    ).all()


@pytest.mark.asyncio
async def test_marketing_send_time_unsubscribe_fails_closed_and_transactional_survives(db_session):
    user, _ = await register(db_session, "marketing@example.com", "strong-password-123", None)
    await service.update_preference(db_session, user, "marketing", True, True, consent=True)
    marketing = await service.create_intent(
        db_session,
        recipient_user_id=user.id,
        notification_type="MARKETING",
        classification=NotificationClass.marketing,
        source_domain="campaigns",
        source_id="campaign-1",
        payload={"subject": "News", "body": "News"},
        channels=(NotificationChannel.email,),
        priority=NotificationPriority.marketing,
    )
    await service.unsubscribe(db_session, user)
    assert await service.deliver_intent(db_session, marketing.id) is DeliveryStatus.suppressed
    reset = await service.create_intent(
        db_session,
        recipient_user_id=user.id,
        notification_type="AUTH_PASSWORD_RESET",
        classification=NotificationClass.transactional,
        source_domain="accounts",
        source_id="reset-1",
        payload={"subject": "Reset", "body": "Reset"},
        channels=(NotificationChannel.email,),
    )
    assert await service._eligible(db_session, reset, user)


@pytest.mark.asyncio
async def test_provider_events_are_replay_safe_and_hard_bounce_suppresses(db_session):
    user, _ = await register(db_session, "bounce@example.com", "strong-password-123", None)
    intent = await service.create_intent(
        db_session,
        recipient_user_id=user.id,
        notification_type="PURCHASE_RECEIPT",
        classification=NotificationClass.transactional,
        source_domain="payments",
        source_id="payment-1",
        payload={"subject": "Receipt", "body": "Receipt"},
        channels=(NotificationChannel.email,),
    )
    attempt = NotificationDeliveryAttempt(
        intent_id=intent.id,
        channel=NotificationChannel.email,
        status=DeliveryStatus.sent,
        attempt_number=1,
        provider="smtp",
        provider_message_id="provider-message-1",
        recipient_snapshot=user.email,
    )
    db_session.add(attempt)
    await db_session.flush()
    assert await service.mark_provider_event(db_session, "provider-message-1", "hard_bounce")
    assert await service.mark_provider_event(db_session, "provider-message-1", "hard_bounce")
    assert attempt.status is DeliveryStatus.failed_permanent
    assert not await service._eligible(db_session, intent, user)


@pytest.mark.asyncio
async def test_opaque_unsubscribe_token_is_tamper_safe_and_does_not_block_transactional(db_session):
    user, _ = await register(db_session, "token@example.com", "strong-password-123", None)
    token = service.unsubscribe_token(user.id)
    assert service.unsubscribe_token_subject(token) == (user.id, "marketing")
    with pytest.raises(ValueError):
        service.unsubscribe_token_subject(f"A{token[1:]}")
    await service.unsubscribe(db_session, user)
    required = await service.create_intent(
        db_session,
        recipient_user_id=user.id,
        notification_type="AUTH_PASSWORD_RESET",
        classification=NotificationClass.transactional,
        source_domain="accounts",
        source_id="reset-token-check",
        payload={"subject": "Reset", "body": "Reset"},
        channels=(NotificationChannel.email,),
    )
    assert await service._eligible(db_session, required, user)


@pytest.mark.asyncio
async def test_internal_notification_target_rejects_open_redirect(db_session):
    user, _ = await register(db_session, "links@example.com", "strong-password-123", None)
    with pytest.raises(ValueError):
        await service.create_intent(
            db_session,
            recipient_user_id=user.id,
            notification_type="MESSAGE_RECEIVED",
            classification=NotificationClass.transactional,
            source_domain="messaging",
            source_id="unsafe-link",
            payload={"target_path": "https://attacker.example"},
        )
