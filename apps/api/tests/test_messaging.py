from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.accounts import service as accounts
from app.content.access import can_access_asset
from app.creators import service as creators
from app.finance import service as finance
from app.main import app
from app.messaging import service as messaging
from app.models.audit import AuditEvent
from app.models.content import (
    AccessPolicy,
    ContentEntitlement,
    ContentItem,
    ContentStatus,
    ContentType,
    DerivativeType,
    MediaAsset,
    MediaDerivative,
    MediaStatus,
    MediaType,
    ModerationStatus,
)
from app.models.creator import CreatorStatus
from app.models.finance import LedgerEntry, PaymentAttempt
from app.models.messaging import (
    AudienceSegment,
    CampaignStatus,
    ConversationParticipant,
    MassMessageCampaign,
    MassMessageRecipient,
    Message,
    MessageReport,
    MessagingPermission,
    UserBlock,
)
from app.models.social import Follow
from app.models.subscription import (
    Subscription,
    SubscriptionDuration,
    SubscriptionPlan,
    SubscriptionStatus,
)


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


@pytest.mark.asyncio
async def test_paid_attachment_unlocks_only_after_settlement(db_session):
    owner, profile = await creator(db_session, "attachment-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "attachment-buyer@example.com", "strong-password-123", None
    )
    initiated = await messaging.send_message(db_session, buyer, profile.id, "hello")
    sent = await messaging.send_in_conversation(
        db_session, owner, initiated.conversation_id, "locked"
    )
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        storage_key="private/phase6-locked",
        original_filename="locked.jpg",
        mime_type="image/jpeg",
    )
    db_session.add(asset)
    await db_session.flush()
    attachment = await messaging.attach_media(db_session, owner, sent.id, asset.id, 700, "EUR")
    assert not await can_access_asset(db_session, asset.id, buyer)
    purchase = await messaging.create_unlock_purchase(db_session, buyer, attachment.id, "unlock-1")
    attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    payload, signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db_session, payload, signature)
    assert purchase.status == "paid"
    assert await can_access_asset(db_session, asset.id, buyer)
    assert await finance.process_development_webhook(db_session, payload, signature) is None


@pytest.mark.asyncio
async def test_attachment_access_exposes_preview_but_not_full_media_before_unlock(db_session):
    owner, profile = await creator(db_session, "attachment-access-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "attachment-access-buyer@example.com", "strong-password-123", None
    )
    initiated = await messaging.send_message(db_session, buyer, profile.id, "hello")
    sent = await messaging.send_in_conversation(
        db_session, owner, initiated.conversation_id, "locked"
    )
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        storage_key="private/phase6-locked-preview",
        original_filename="locked.jpg",
        mime_type="image/jpeg",
    )
    db_session.add(asset)
    await db_session.flush()
    db_session.add_all(
        [
            MediaDerivative(
                media_asset_id=asset.id,
                derivative_type=DerivativeType.blurred_preview,
                status=MediaStatus.ready,
                storage_key="derivative/preview.webp",
                mime_type="image/webp",
            ),
            MediaDerivative(
                media_asset_id=asset.id,
                derivative_type=DerivativeType.display,
                status=MediaStatus.ready,
                storage_key="derivative/display.webp",
                mime_type="image/webp",
            ),
        ]
    )
    attachment = await messaging.attach_media(db_session, owner, sent.id, asset.id, 700, "EUR")
    await db_session.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as buyer_client:
        await buyer_client.post(
            "/api/v1/auth/login",
            json={"email": buyer.email, "password": "strong-password-123"},
        )
        access = await buyer_client.get(f"/api/v1/messages/attachments/{attachment.id}/access")
        assert access.status_code == 200
        assert access.json()["locked"] is True
        assert access.json()["preview_delivery_path"]
        assert access.json()["full_delivery_path"] is None
        purchase = await messaging.create_unlock_purchase(
            db_session, buyer, attachment.id, "unlock-access"
        )
        attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
        payload, signature = finance.development_webhook_payload(attempt)
        await finance.process_development_webhook(db_session, payload, signature)
        await db_session.commit()
        unlocked = await buyer_client.get(f"/api/v1/messages/attachments/{attachment.id}/access")
        assert unlocked.status_code == 200
        assert unlocked.json()["locked"] is False
        assert unlocked.json()["full_delivery_path"].startswith("/media/derivatives/")


@pytest.mark.asyncio
async def test_campaign_snapshots_recipients_and_replay_respects_later_blocks(db_session):
    owner, profile = await creator(db_session, "campaign-owner@example.com")
    follower, _ = await accounts.register(
        db_session, "campaign-follower@example.com", "strong-password-123", None
    )
    blocked, _ = await accounts.register(
        db_session, "campaign-blocked@example.com", "strong-password-123", None
    )
    db_session.add_all(
        [
            Follow(user_id=follower.id, creator_id=profile.id),
            Follow(user_id=blocked.id, creator_id=profile.id),
        ]
    )
    await db_session.flush()
    campaign = MassMessageCampaign(
        creator_id=profile.id,
        created_by_user_id=owner.id,
        audience_segment=AudienceSegment.followers,
        body="Campaign announcement",
        status=CampaignStatus.scheduled,
        scheduled_at=datetime.now(UTC) + timedelta(hours=1),
    )
    db_session.add(campaign)
    await db_session.flush()
    assert await messaging.snapshot_campaign_recipients(db_session, campaign) == 2
    await db_session.execute(
        Follow.__table__.delete().where(
            Follow.user_id == follower.id, Follow.creator_id == profile.id
        )
    )
    db_session.add(UserBlock(blocker_user_id=owner.id, blocked_user_id=blocked.id))
    await db_session.flush()
    assert await messaging.execute_campaign(db_session, campaign.id) == 1
    assert await messaging.execute_campaign(db_session, campaign.id) == 0
    recipients = (
        await db_session.scalars(
            select(MassMessageRecipient).where(MassMessageRecipient.campaign_id == campaign.id)
        )
    ).all()
    assert {row.recipient_user_id for row in recipients} == {follower.id, blocked.id}
    assert sum(row.message_id is not None for row in recipients) == 1
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Message).where(Message.body == "Campaign announcement")
        )
        == 1
    )


@pytest.mark.asyncio
async def test_campaign_segments_use_authoritative_history_and_exclude_blocks(db_session):
    owner, profile = await creator(db_session, "campaign-segments-owner@example.com")
    follower, _ = await accounts.register(
        db_session, "campaign-segments-follower@example.com", "strong-password-123", None
    )
    active, _ = await accounts.register(
        db_session, "campaign-segments-active@example.com", "strong-password-123", None
    )
    expired, _ = await accounts.register(
        db_session, "campaign-segments-expired@example.com", "strong-password-123", None
    )
    purchaser, _ = await accounts.register(
        db_session, "campaign-segments-purchaser@example.com", "strong-password-123", None
    )
    blocked, _ = await accounts.register(
        db_session, "campaign-segments-blocked@example.com", "strong-password-123", None
    )
    db_session.add_all(
        [
            Follow(user_id=follower.id, creator_id=profile.id),
            Follow(user_id=blocked.id, creator_id=profile.id),
            ContentEntitlement(
                subject_user_id=active.id,
                creator_id=profile.id,
                source_type="subscription",
                source_reference="active-period",
                valid_from=datetime.now(UTC) - timedelta(days=1),
            ),
            ContentEntitlement(
                subject_user_id=blocked.id,
                creator_id=profile.id,
                source_type="subscription",
                source_reference="blocked-period",
                valid_from=datetime.now(UTC) - timedelta(days=1),
            ),
            UserBlock(blocker_user_id=owner.id, blocked_user_id=blocked.id),
        ]
    )
    plan = SubscriptionPlan(creator_id=profile.id, currency="EUR", enabled=True)
    db_session.add(plan)
    await db_session.flush()
    db_session.add(
        Subscription(
            subscriber_user_id=expired.id,
            creator_id=profile.id,
            plan_id=plan.id,
            duration=SubscriptionDuration.month_1,
            currency="EUR",
            status=SubscriptionStatus.expired,
        )
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Campaign PPV",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=500,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    purchase = await finance.initiate_purchase(
        db_session, purchaser, content.id, "campaign-purchase"
    )
    attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    payload, signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db_session, payload, signature)
    for segment, expected in (
        (AudienceSegment.followers, {follower.id}),
        (AudienceSegment.active_subscribers, {active.id}),
        (AudienceSegment.expired_subscribers, {expired.id}),
        (AudienceSegment.previous_customers, {purchaser.id}),
    ):
        campaign = MassMessageCampaign(
            creator_id=profile.id,
            created_by_user_id=owner.id,
            audience_segment=segment,
            body=segment.value,
            status=CampaignStatus.draft,
        )
        db_session.add(campaign)
        await db_session.flush()
        assert set(await messaging.campaign_recipients(db_session, campaign)) == expected


@pytest.mark.asyncio
async def test_due_campaign_worker_replay_cannot_duplicate_recipient_messages(db_session):
    owner, profile = await creator(db_session, "campaign-worker-owner@example.com")
    recipient, _ = await accounts.register(
        db_session, "campaign-worker-recipient@example.com", "strong-password-123", None
    )
    db_session.add(Follow(user_id=recipient.id, creator_id=profile.id))
    campaign = MassMessageCampaign(
        creator_id=profile.id,
        created_by_user_id=owner.id,
        audience_segment=AudienceSegment.followers,
        body="Worker replay safe",
        status=CampaignStatus.scheduled,
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db_session.add(campaign)
    await db_session.flush()
    assert await messaging.snapshot_campaign_recipients(db_session, campaign) == 1
    assert await messaging.execute_due_campaigns(db_session) == 1
    assert await messaging.execute_due_campaigns(db_session) == 0
    assert (
        await db_session.scalar(
            select(func.count()).select_from(Message).where(Message.body == "Worker replay safe")
        )
        == 1
    )


@pytest.mark.asyncio
async def test_message_reports_and_moderator_access_are_authorized_and_audited(db_session):
    _owner, profile = await creator(db_session, "report-owner@example.com")
    viewer, _ = await accounts.register(
        db_session, "report-viewer@example.com", "strong-password-123", None
    )
    message = await messaging.send_message(db_session, viewer, profile.id, "reportable message")
    moderator, _ = await accounts.register(
        db_session, "report-moderator@example.com", "strong-password-123", None
    )
    await accounts.assign_role(db_session, moderator, "moderator", moderator.id, None)
    await db_session.commit()
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as viewer_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as moderator_client,
    ):
        await viewer_client.post(
            "/api/v1/auth/login",
            json={"email": viewer.email, "password": "strong-password-123"},
        )
        await moderator_client.post(
            "/api/v1/auth/login",
            json={"email": moderator.email, "password": "strong-password-123"},
        )
        assert (
            await viewer_client.post(
                f"/api/v1/messages/messages/{message.id}/report",
                json={"reason": "abuse"},
            )
        ).status_code == 200
        assert (
            await viewer_client.post(
                f"/api/v1/messages/messages/{message.id}/report",
                json={"reason": "abuse"},
            )
        ).status_code == 200
        assert (
            await viewer_client.get(f"/api/v1/admin/messages/{message.id}?reason=review")
        ).status_code == 403
        opened = await moderator_client.get(
            f"/api/v1/admin/messages/{message.id}?reason=abuse-review"
        )
        assert opened.status_code == 200
        assert opened.json()["body"] == "reportable message"
    assert (await db_session.scalar(select(func.count()).select_from(MessageReport))) == 1
    access = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "message.moderator_accessed")
    )
    assert access and access.actor_user_id == moderator.id
