from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.accounts.service import register
from app.compliance.policy import create_jurisdiction_revision, create_template_revision
from app.compliance.types import PolicyOverrides, PolicyRules
from app.models.compliance import CompliancePolicyStatus, CompliancePolicyTemplateRevision
from app.models.notification import (
    DeliveryStatus,
    InAppNotification,
    NotificationChannel,
    NotificationClass,
    NotificationDeliveryAttempt,
    NotificationPriority,
)
from app.notifications import service


async def publish_marketing_policy(db, *, enabled: bool) -> None:
    revision = await db.scalar(
        select(CompliancePolicyTemplateRevision)
        .order_by(CompliancePolicyTemplateRevision.version.desc())
        .limit(1)
    )
    assert revision is not None
    assert revision.reviewed_by_user_id is not None
    now = datetime.now(UTC)
    rules = PolicyRules.model_validate(revision.rules_json).model_copy(
        update={"marketing_email_allowed": enabled}
    )
    successor = await create_template_revision(
        db,
        template_id=revision.template_id,
        rules=rules,
        status=CompliancePolicyStatus.active,
        effective_from=now - timedelta(seconds=1),
        effective_until=None,
        actor_user_id=revision.reviewed_by_user_id,
        reviewed_at=now,
        reviewed_by_user_id=revision.reviewed_by_user_id,
        change_reason="Change marketing-email eligibility for delivery test",
        is_demo=True,
    )
    await create_jurisdiction_revision(
        db,
        country_code="PT",
        template_revision_id=successor.id,
        overrides=PolicyOverrides(),
        status=CompliancePolicyStatus.active,
        effective_from=now - timedelta(seconds=1),
        effective_until=None,
        actor_user_id=revision.reviewed_by_user_id,
        reviewed_at=now,
        reviewed_by_user_id=revision.reviewed_by_user_id,
        change_reason="Change marketing-email eligibility for delivery test",
        is_demo=True,
    )


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
async def test_marketing_policy_is_checked_at_delivery_without_blocking_transactional(
    db_session,
):
    user, _ = await register(
        db_session,
        "marketing-policy@example.com",
        "strong-password-123",
        None,
        adult_confirmed=True,
        country_code="PT",
    )
    await service.update_preference(db_session, user, "marketing", True, True, consent=True)
    marketing = await service.create_intent(
        db_session,
        recipient_user_id=user.id,
        notification_type="MARKETING",
        classification=NotificationClass.marketing,
        source_domain="campaigns",
        source_id="policy-disabled",
        payload={"subject": "News", "body": "News"},
        channels=(NotificationChannel.email,),
        priority=NotificationPriority.marketing,
    )
    transactional = await service.create_intent(
        db_session,
        recipient_user_id=user.id,
        notification_type="AUTH_PASSWORD_RESET",
        classification=NotificationClass.transactional,
        source_domain="accounts",
        source_id="policy-disabled-reset",
        payload={"subject": "Reset", "body": "Reset"},
        channels=(NotificationChannel.email,),
    )
    await publish_marketing_policy(db_session, enabled=False)

    assert await service.deliver_intent(db_session, marketing.id) is DeliveryStatus.suppressed
    attempt = await db_session.scalar(
        select(NotificationDeliveryAttempt).where(
            NotificationDeliveryAttempt.intent_id == marketing.id
        )
    )
    assert attempt is not None
    assert attempt.status is DeliveryStatus.suppressed
    assert await service._eligible(db_session, transactional, user)


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
