import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from conftest import trusted_self_attested_accounts as accounts
from fastapi import HTTPException
from sqlalchemy import func, select
from starlette.requests import Request
from surface_policy_helpers import publish_creator_identity_policy

from app.accounts import adult_access
from app.api.routes import messaging as messaging_routes
from app.compliance.types import ComplianceDecision
from app.content.access import can_access_asset
from app.core.config import get_settings
from app.core.rate_limit import enforce_messaging_rate_limit
from app.creators import service as creators
from app.finance import service as finance
from app.main import app
from app.messaging import service as messaging
from app.models.audit import AuditEvent
from app.models.compliance import AgeAssuranceLevel, ComplianceFeature
from app.models.content import (
    AccessPolicy,
    ContentEntitlement,
    ContentItem,
    ContentStatus,
    ContentType,
    DerivativeType,
    Gallery,
    GalleryItem,
    MediaAsset,
    MediaAudience,
    MediaDerivative,
    MediaStatus,
    MediaType,
    ModerationStatus,
)
from app.models.creator import CreatorStatus, CreatorVerification, VerificationStatus
from app.models.finance import (
    LedgerEntry,
    LedgerTransaction,
    LedgerTransactionType,
    PaymentAttempt,
    PaymentRefundRequirement,
    PaymentStatus,
)
from app.models.messaging import (
    AudienceSegment,
    CampaignStatus,
    Conversation,
    ConversationParticipant,
    MassMessageCampaign,
    MassMessageRecipient,
    Message,
    MessageAttachment,
    MessageReport,
    MessageUnlockPurchase,
    MessagingPermission,
    PendingMessageSend,
    UserBlock,
)
from app.models.social import Follow
from app.models.subscription import (
    Subscription,
    SubscriptionDuration,
    SubscriptionPlan,
    SubscriptionStatus,
)


def denied_messaging_decision() -> ComplianceDecision:
    return ComplianceDecision(
        allowed=False,
        code="AGE_VERIFICATION_REQUIRED",
        action="VERIFY_AGE",
        reason="Age verification is required",
        feature=ComplianceFeature.messaging,
        jurisdiction="PT",
        policy_id=None,
        policy_version=1,
        required_minimum_age=18,
        required_assurance_level=AgeAssuranceLevel.self_attested,
        achieved_assurance_level=AgeAssuranceLevel.none,
        age_access_allowed=False,
        feature_allowed=True,
        country_conflict=False,
        verification_expires_at=None,
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
    profile.is_public = True
    await db.flush()
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
    original_ledger_id = pending.ledger_transaction_id
    refund_payload = json.dumps(
        {
            "id": f"provider-message-refund-{attempt.id}",
            "type": "payment.refunded",
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    refund_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(), refund_payload, hashlib.sha256
    ).hexdigest()
    await finance.process_development_webhook(db_session, refund_payload, refund_signature)
    assert pending.status == "refunded"
    assert attempt.status is PaymentStatus.refunded
    assert await db_session.scalar(select(func.count()).select_from(Message)) == 1
    reversal = await db_session.scalar(
        select(LedgerTransaction).where(
            LedgerTransaction.reversal_of_transaction_id == original_ledger_id,
            LedgerTransaction.transaction_type == LedgerTransactionType.refund,
        )
    )
    assert reversal


@pytest.mark.asyncio
async def test_signed_messaging_disputes_deny_delivery_and_reverse_once(db_session):
    owner, profile = await creator(db_session, "message-dispute-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "message-dispute-buyer@example.com", "strong-password-123", None
    )
    settings = await messaging.settings_for_creator(db_session, profile.id)
    settings.permission = MessagingPermission.anyone
    settings.send_fee_minor, settings.send_fee_currency = 250, "EUR"

    message_count = await db_session.scalar(select(func.count()).select_from(Message))
    pending = await messaging.initiate_paid_send(
        db_session, buyer, profile.id, "must remain undelivered", "message-dispute-send"
    )
    pending_attempt = await db_session.get(PaymentAttempt, pending.payment_attempt_id)
    assert pending_attempt
    dispute_payload, dispute_signature = signed_payment_event(
        pending_attempt, "payment.disputed", f"message-send-dispute-{pending_attempt.id}"
    )
    await finance.process_development_webhook(db_session, dispute_payload, dispute_signature)
    assert pending.status == "disputed"
    assert pending_attempt.status is PaymentStatus.disputed
    late_success, late_success_signature = signed_payment_event(
        pending_attempt,
        "payment.succeeded",
        f"message-send-late-success-{pending_attempt.id}",
    )
    await finance.process_development_webhook(db_session, late_success, late_success_signature)
    assert pending.status == "disputed"
    assert pending.ledger_transaction_id is None
    assert pending.message_id is None
    assert await db_session.scalar(select(func.count()).select_from(Message)) == message_count
    chargeback_payload, chargeback_signature = signed_payment_event(
        pending_attempt,
        "payment.chargeback",
        f"message-send-chargeback-{pending_attempt.id}",
    )
    await finance.process_development_webhook(db_session, chargeback_payload, chargeback_signature)
    requirement = await db_session.scalar(
        select(PaymentRefundRequirement).where(
            PaymentRefundRequirement.payment_attempt_id == pending_attempt.id
        )
    )
    assert requirement and requirement.status.value == "completed"
    assert pending.status == "failed"
    assert pending_attempt.status is PaymentStatus.chargeback
    assert await db_session.scalar(select(func.count()).select_from(Message)) == message_count

    initiated = await messaging.send_message(db_session, buyer, profile.id, "hello")
    locked_message = await messaging.send_in_conversation(
        db_session, owner, initiated.conversation_id, "locked"
    )
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        storage_key="private/message-dispute-locked",
        original_filename="dispute.jpg",
        mime_type="image/jpeg",
    )
    db_session.add(asset)
    await db_session.flush()
    attachment = await messaging.attach_media(
        db_session, owner, locked_message.id, asset.id, 700, "EUR"
    )
    unlock = await messaging.create_unlock_purchase(
        db_session, buyer, attachment.id, "message-dispute-unlock"
    )
    unlock_attempt = await db_session.get(PaymentAttempt, unlock.payment_attempt_id)
    assert unlock_attempt
    success_payload, success_signature = finance.development_webhook_payload(unlock_attempt)
    await finance.process_development_webhook(db_session, success_payload, success_signature)
    decision = adult_access.resolve_adult_access(buyer, None)
    assert unlock.status == "paid"
    assert await can_access_asset(db_session, asset.id, buyer, decision)
    original_ledger_id = unlock.ledger_transaction_id
    assert original_ledger_id

    unlock_dispute, unlock_dispute_signature = signed_payment_event(
        unlock_attempt,
        "payment.disputed",
        f"message-unlock-dispute-{unlock_attempt.id}",
    )
    await finance.process_development_webhook(db_session, unlock_dispute, unlock_dispute_signature)
    assert unlock.status == "disputed"
    assert unlock_attempt.status is PaymentStatus.disputed
    assert not await can_access_asset(db_session, asset.id, buyer, decision)
    assert (
        await db_session.scalar(
            select(LedgerTransaction.id).where(
                LedgerTransaction.reversal_of_transaction_id == original_ledger_id
            )
        )
        is None
    )

    unlock_refund, unlock_refund_signature = signed_payment_event(
        unlock_attempt,
        "payment.refunded",
        f"message-unlock-refund-{unlock_attempt.id}",
    )
    await finance.process_development_webhook(db_session, unlock_refund, unlock_refund_signature)
    assert unlock.status == "refunded"
    assert unlock_attempt.status is PaymentStatus.refunded
    unlock_chargeback, unlock_chargeback_signature = signed_payment_event(
        unlock_attempt,
        "payment.chargeback",
        f"message-unlock-chargeback-{unlock_attempt.id}",
    )
    await finance.process_development_webhook(
        db_session, unlock_chargeback, unlock_chargeback_signature
    )
    assert unlock.status == "chargeback"
    assert unlock_attempt.status is PaymentStatus.chargeback
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(LedgerTransaction)
            .where(LedgerTransaction.reversal_of_transaction_id == original_ledger_id)
        )
        == 1
    )


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
async def test_ppv_content_asset_cannot_be_reused_as_message_attachment(db_session):
    owner, profile = await creator(db_session, "message-media-isolation@example.com")
    viewer, _ = await accounts.register(
        db_session,
        "message-media-isolation-viewer@example.com",
        "strong-password-123",
        None,
    )
    initiated = await messaging.send_message(db_session, viewer, profile.id, "hello")
    sent = await messaging.send_in_conversation(
        db_session, owner, initiated.conversation_id, "attachment"
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Paid message source",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=700,
        price_currency="EUR",
    )
    content.gallery = Gallery(preview_count=0)
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        moderation_status=ModerationStatus.approved,
        audience=MediaAudience.adult_restricted,
        storage_key="original/message-isolation",
        original_filename="message-isolation.jpg",
        mime_type="image/jpeg",
    )
    db_session.add_all([content, asset])
    await db_session.flush()
    db_session.add(
        GalleryItem(
            gallery_id=content.gallery.id,
            media_asset_id=asset.id,
            position=0,
        )
    )
    await db_session.flush()

    with pytest.raises(messaging.MessagingError, match="dedicated to one content"):
        await messaging.attach_media(db_session, owner, sent.id, asset.id, 700, "EUR")

    assert (
        await db_session.scalar(
            select(MessageAttachment.id).where(MessageAttachment.media_asset_id == asset.id)
        )
        is None
    )


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
    decision = adult_access.resolve_adult_access(buyer, None)
    assert not await can_access_asset(db_session, asset.id, buyer, decision)
    purchase = await messaging.create_unlock_purchase(db_session, buyer, attachment.id, "unlock-1")
    attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    payload, signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db_session, payload, signature)
    assert purchase.status == "paid"
    assert await can_access_asset(db_session, asset.id, buyer, decision)
    db_session.add(UserBlock(blocker_user_id=owner.id, blocked_user_id=buyer.id))
    await db_session.flush()
    assert not await can_access_asset(db_session, asset.id, buyer, decision)
    assert await finance.process_development_webhook(db_session, payload, signature) is None


@pytest.mark.parametrize(
    "containment", ["private", "status", "kyc", "buyer_blocks", "creator_blocks"]
)
@pytest.mark.asyncio
async def test_messaging_commands_require_current_public_creator_access(db_session, containment):
    slug = {
        "private": "p",
        "status": "s",
        "kyc": "k",
        "buyer_blocks": "bb",
        "creator_blocks": "cb",
    }[containment]
    owner, profile = await creator(db_session, f"pc-{slug}@example.com")
    buyer, _ = await accounts.register(
        db_session,
        f"pb-{slug}@example.com",
        "strong-password-123",
        None,
    )
    settings = await messaging.settings_for_creator(db_session, profile.id)
    settings.permission = MessagingPermission.anyone
    settings.send_fee_minor, settings.send_fee_currency = 250, "EUR"
    initiated = await messaging.send_message(db_session, buyer, profile.id, "hello")
    sent = await messaging.send_in_conversation(
        db_session, owner, initiated.conversation_id, "locked"
    )
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        storage_key=f"private/contained-{containment}",
        original_filename="contained.jpg",
        mime_type="image/jpeg",
    )
    db_session.add(asset)
    await db_session.flush()
    attachment = await messaging.attach_media(db_session, owner, sent.id, asset.id, 700, "EUR")
    if containment == "private":
        profile.is_public = False
    elif containment == "status":
        profile.status = CreatorStatus.suspended
    elif containment == "kyc":
        await publish_creator_identity_policy(db_session)
        db_session.add(
            CreatorVerification(
                creator_profile_id=profile.id,
                provider="test-expiry",
                provider_reference=f"contained-{containment}",
                status=VerificationStatus.expired,
                adult_verified=False,
                created_at=datetime.now(UTC) + timedelta(seconds=1),
            )
        )
    else:
        db_session.add(
            UserBlock(
                blocker_user_id=buyer.id if containment == "buyer_blocks" else owner.id,
                blocked_user_id=owner.id if containment == "buyer_blocks" else buyer.id,
            )
        )
    await db_session.flush()
    attempts_before = await db_session.scalar(select(func.count()).select_from(PaymentAttempt))
    messages_before = await db_session.scalar(select(func.count()).select_from(Message))

    with pytest.raises(PermissionError, match="Messaging is not permitted"):
        await messaging.send_message(db_session, buyer, profile.id, "new free message")
    with pytest.raises(PermissionError, match="Messaging is not permitted"):
        await messaging.initiate_paid_send(
            db_session, buyer, profile.id, "paid", f"contained-send-{containment}"
        )
    with pytest.raises(PermissionError, match="Locked attachment not found"):
        await messaging.create_unlock_purchase(
            db_session, buyer, attachment.id, f"contained-unlock-{containment}"
        )
    assert (
        await db_session.scalar(select(func.count()).select_from(PaymentAttempt)) == attempts_before
    )
    assert await db_session.scalar(select(func.count()).select_from(Message)) == messages_before
    assert await db_session.scalar(select(PendingMessageSend.id)) is None
    assert await db_session.scalar(select(MessageUnlockPurchase.id)) is None


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
    buyer.email_verified_at = accounts._now()
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
        refund_payload = json.dumps(
            {
                "id": f"provider-unlock-refund-{attempt.id}",
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
        await db_session.commit()
        locked_again = await buyer_client.get(
            f"/api/v1/messages/attachments/{attachment.id}/access"
        )
        assert locked_again.status_code == 200
        assert locked_again.json()["locked"] is True
        assert locked_again.json()["full_delivery_path"] is None


@pytest.mark.asyncio
async def test_attachment_compliance_denial_is_structured_and_leaks_no_media_path(
    db_session, monkeypatch, reviewed_pt_compliance_policy
):
    owner, profile = await creator(db_session, "attachment-denial-owner@example.com")
    buyer, _ = await accounts.register(
        db_session,
        "attachment-denial-buyer@example.com",
        "strong-password-123",
        None,
        country_code="PT",
    )
    initiated = await messaging.send_message(db_session, buyer, profile.id, "hello")
    sent = await messaging.send_in_conversation(
        db_session, owner, initiated.conversation_id, "restricted attachment"
    )
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        audience=MediaAudience.adult_restricted,
        storage_key="private/restricted-message-original",
        original_filename="restricted.jpg",
        mime_type="image/jpeg",
    )
    db_session.add(asset)
    await db_session.flush()
    preview = MediaDerivative(
        media_asset_id=asset.id,
        derivative_type=DerivativeType.blurred_preview,
        status=MediaStatus.ready,
        storage_key="derivative/restricted-message-preview",
        mime_type="image/webp",
    )
    full = MediaDerivative(
        media_asset_id=asset.id,
        derivative_type=DerivativeType.display,
        status=MediaStatus.ready,
        storage_key="derivative/restricted-message-full",
        mime_type="image/webp",
    )
    db_session.add_all([preview, full])
    attachment = await messaging.attach_media(db_session, owner, sent.id, asset.id, 700, "EUR")
    await db_session.flush()

    async def denied(*_args, **_kwargs):
        return denied_messaging_decision()

    monkeypatch.setattr(messaging_routes, "request_attachment_decision", denied)
    response = await messaging_routes.attachment_access(
        attachment.id,
        Request({"type": "http", "client": ("127.0.0.1", 50000), "headers": []}),
        (buyer, None),
        db_session,
    )
    payload = response.model_dump(mode="json")
    assert payload["compliance_allowed"] is False
    assert payload["compliance_code"] == "AGE_VERIFICATION_REQUIRED"
    assert payload["compliance_action"] == "VERIFY_AGE"
    assert payload["compliance_reason"] == "Age verification is required"
    assert payload["preview_delivery_path"] is None
    assert payload["full_delivery_path"] is None
    assert str(preview.id) not in str(payload)
    assert str(full.id) not in str(payload)
    assert asset.storage_key not in str(payload)


@pytest.mark.asyncio
async def test_corrupt_content_message_asset_hides_preview_and_access(db_session, monkeypatch):
    owner, profile = await creator(db_session, "ambiguous-message-owner@example.com")
    buyer, _ = await accounts.register(
        db_session,
        "ambiguous-message-buyer@example.com",
        "strong-password-123",
        None,
    )
    initiated = await messaging.send_message(db_session, buyer, profile.id, "hello")
    sent = await messaging.send_in_conversation(
        db_session, owner, initiated.conversation_id, "ambiguous attachment"
    )
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        audience=MediaAudience.safe_public,
        storage_key="private/ambiguous-message-original",
        original_filename="ambiguous-message.jpg",
        mime_type="image/jpeg",
    )
    db_session.add(asset)
    await db_session.flush()
    db_session.add(
        MediaDerivative(
            media_asset_id=asset.id,
            derivative_type=DerivativeType.blurred_preview,
            status=MediaStatus.ready,
            storage_key="derivative/ambiguous-message-preview",
            mime_type="image/webp",
        )
    )
    attachment = await messaging.attach_media(db_session, owner, sent.id, asset.id, None, None)
    corrupt_content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.free,
        title="Corrupt duplicate context",
    )
    corrupt_content.gallery = Gallery(
        items=[GalleryItem(media_asset_id=asset.id, position=0, is_preview=True)]
    )
    db_session.add(corrupt_content)
    await db_session.flush()

    class Storage:
        def create_download_url(self, *_args, **_kwargs):
            raise AssertionError("Ambiguous message media must not mint a storage URL")

    monkeypatch.setattr(messaging_routes, "storage_provider", lambda: Storage())
    request = Request({"type": "http", "client": ("127.0.0.1", 50000), "headers": []})
    for handler in (messaging_routes.attachment_access, messaging_routes.attachment_preview):
        with pytest.raises(HTTPException) as blocked:
            await handler(attachment.id, request, (buyer, None), db_session)
        assert blocked.value.status_code == 404


@pytest.mark.asyncio
async def test_messaging_policy_denial_creates_no_conversation_or_message(db_session, monkeypatch):
    _owner, profile = await creator(db_session, "message-policy-owner@example.com")
    viewer, _ = await accounts.register(
        db_session, "message-policy-viewer@example.com", "strong-password-123", None
    )
    conversation_count = await db_session.scalar(select(func.count()).select_from(Conversation))
    message_count = await db_session.scalar(select(func.count()).select_from(Message))

    async def denied(*_args, **_kwargs):
        return denied_messaging_decision()

    monkeypatch.setattr(messaging, "resolve_compliance_decision", denied)
    with pytest.raises(messaging.MessagingError) as exc:
        await messaging.send_message(db_session, viewer, profile.id, "must not be persisted")

    assert exc.value.code == "AGE_VERIFICATION_REQUIRED"
    assert exc.value.action == "VERIFY_AGE"
    assert (
        await db_session.scalar(select(func.count()).select_from(Conversation))
        == conversation_count
    )
    assert await db_session.scalar(select(func.count()).select_from(Message)) == message_count


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
async def test_campaign_policy_denial_preserves_schedule_and_delivers_nothing(
    db_session, monkeypatch
):
    owner, profile = await creator(db_session, "campaign-policy-owner@example.com")
    recipient, _ = await accounts.register(
        db_session, "campaign-policy-recipient@example.com", "strong-password-123", None
    )
    db_session.add(Follow(user_id=recipient.id, creator_id=profile.id))
    campaign = MassMessageCampaign(
        creator_id=profile.id,
        created_by_user_id=owner.id,
        audience_segment=AudienceSegment.followers,
        body="Policy-denied campaign",
        status=CampaignStatus.scheduled,
        scheduled_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    db_session.add(campaign)
    await db_session.flush()
    assert await messaging.snapshot_campaign_recipients(db_session, campaign) == 1

    async def denied(*_args, **_kwargs):
        return denied_messaging_decision()

    monkeypatch.setattr(messaging, "resolve_compliance_decision", denied)
    with pytest.raises(messaging.MessagingError) as exc:
        await messaging.execute_campaign(db_session, campaign.id)

    assert exc.value.code == "AGE_VERIFICATION_REQUIRED"
    assert campaign.status is CampaignStatus.scheduled
    assert campaign.started_at is None
    assert campaign.completed_at is None
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(Message)
            .where(Message.body == "Policy-denied campaign")
        )
        == 0
    )
    campaign_recipient = await db_session.scalar(
        select(MassMessageRecipient).where(
            MassMessageRecipient.campaign_id == campaign.id,
            MassMessageRecipient.recipient_user_id == recipient.id,
        )
    )
    assert campaign_recipient and campaign_recipient.message_id is None
    assert campaign_recipient.delivered_at is None


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
async def test_messaging_write_rate_limit_is_scoped_to_the_messaging_action():
    settings = get_settings()
    original_attempts = settings.messaging_rate_limit_attempts
    original_window = settings.messaging_rate_limit_window_seconds
    settings.messaging_rate_limit_attempts = 1
    settings.messaging_rate_limit_window_seconds = 60
    request = Request({"type": "http", "client": ("127.0.0.1", 50000), "headers": []})
    try:
        await enforce_messaging_rate_limit(request, "rate-limit-user", "send")
        await enforce_messaging_rate_limit(request, "rate-limit-user", "report")
        with pytest.raises(HTTPException) as limited:
            await enforce_messaging_rate_limit(request, "rate-limit-user", "send")
        assert limited.value.status_code == 429
    finally:
        settings.messaging_rate_limit_attempts = original_attempts
        settings.messaging_rate_limit_window_seconds = original_window


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
    viewer.email_verified_at = moderator.email_verified_at = accounts._now()
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
