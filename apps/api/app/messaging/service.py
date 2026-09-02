from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.compliance.policy import resolve_compliance_decision
from app.compliance.types import (
    ComplianceAccessError,
    ComplianceDecision,
    require_compliance_access,
)
from app.core.config import get_settings
from app.creators.service import require_public_creator_access
from app.finance.providers import new_provider_reference
from app.finance.service import (
    _account,
    commission_amount,
    currency_code,
    post_entries,
)
from app.media.contexts import require_media_context_available
from app.models.compliance import ComplianceFeature
from app.models.content import (
    ContentEntitlement,
    EntitlementStatus,
    MediaAsset,
    MediaAudience,
    MediaStatus,
)
from app.models.creator import CreatorProfile, CreatorStatus
from app.models.finance import (
    CommissionRule,
    LedgerAccountKind,
    LedgerDirection,
    LedgerTransactionType,
    PaymentAttempt,
)
from app.models.identity import User
from app.models.messaging import (
    AudienceSegment,
    CampaignStatus,
    Conversation,
    ConversationParticipant,
    MassMessageCampaign,
    MassMessageRecipient,
    Message,
    MessageAttachment,
    MessageType,
    MessageUnlockPurchase,
    MessagingPermission,
    MessagingSettings,
    PendingMessageSend,
    UserBlock,
)
from app.models.social import Follow
from app.models.subscription import (
    Subscription,
    SubscriptionPeriod,
    SubscriptionPeriodStatus,
    SubscriptionStatus,
)
from app.notifications.service import emit_transactional


class MessagingError(ValueError):
    def __init__(self, message: str, compliance_decision: ComplianceDecision | None = None):
        super().__init__(message)
        self.compliance_decision = compliance_decision
        self.code = compliance_decision.code if compliance_decision else None
        self.action = compliance_decision.action if compliance_decision else None


async def require_messaging_compliance(
    db: AsyncSession,
    user: User,
    *,
    adult_restricted: bool,
    decisions: dict[ComplianceFeature, ComplianceDecision] | None = None,
) -> ComplianceDecision:
    allowed: ComplianceDecision | None = None
    for feature in (ComplianceFeature.messaging, ComplianceFeature.purchases):
        decision = (
            decisions.get(feature)
            if decisions
            else await resolve_compliance_decision(
                db,
                user=user,
                feature=feature,
                adult_restricted=adult_restricted,
            )
        )
        if decision is None:
            raise MessagingError("Messaging compliance decision is unavailable")
        try:
            allowed = require_compliance_access(decision)
        except ComplianceAccessError as exc:
            raise MessagingError(exc.decision.reason, exc.decision) from exc
    assert allowed is not None
    return allowed


async def require_messaging_feature(
    db: AsyncSession,
    user: User,
    *,
    adult_restricted: bool = False,
    decision: ComplianceDecision | None = None,
) -> ComplianceDecision:
    resolved = decision or await resolve_compliance_decision(
        db,
        user=user,
        feature=ComplianceFeature.messaging,
        adult_restricted=adult_restricted,
    )
    try:
        return require_compliance_access(resolved)
    except ComplianceAccessError as exc:
        raise MessagingError(exc.decision.reason, exc.decision) from exc


async def creator_for_user(db: AsyncSession, user: User) -> CreatorProfile:
    creator = await db.scalar(select(CreatorProfile).where(CreatorProfile.user_id == user.id))
    if not creator or creator.status is not CreatorStatus.approved:
        raise PermissionError("Approved creator access is required")
    return creator


async def settings_for_creator(db: AsyncSession, creator_id: UUID) -> MessagingSettings:
    value = await db.scalar(
        select(MessagingSettings).where(MessagingSettings.creator_id == creator_id)
    )
    if not value:
        value = MessagingSettings(creator_id=creator_id)
        db.add(value)
        await db.flush()
    return value


async def is_blocked(db: AsyncSession, first_user_id: UUID, second_user_id: UUID) -> bool:
    return (
        await db.scalar(
            select(UserBlock.id).where(
                or_(
                    and_(
                        UserBlock.blocker_user_id == first_user_id,
                        UserBlock.blocked_user_id == second_user_id,
                    ),
                    and_(
                        UserBlock.blocker_user_id == second_user_id,
                        UserBlock.blocked_user_id == first_user_id,
                    ),
                )
            )
        )
        is not None
    )


async def active_subscriber(db: AsyncSession, user_id: UUID, creator_id: UUID) -> bool:
    now = datetime.now(UTC)
    return (
        await db.scalar(
            select(ContentEntitlement.id).where(
                ContentEntitlement.subject_user_id == user_id,
                ContentEntitlement.creator_id == creator_id,
                ContentEntitlement.status == EntitlementStatus.active,
                ContentEntitlement.valid_from <= now,
                or_(ContentEntitlement.valid_until.is_(None), ContentEntitlement.valid_until > now),
            )
        )
        is not None
    )


async def previous_customer(db: AsyncSession, user_id: UUID, creator_id: UUID) -> bool:
    """Only settled PPV or completed subscription periods qualify; failed attempts never do."""
    from app.models.finance import Purchase, PurchaseStatus

    ppv = exists(
        select(Purchase.id).where(
            Purchase.buyer_user_id == user_id,
            Purchase.seller_creator_id == creator_id,
            Purchase.status == PurchaseStatus.paid,
        )
    )
    subscription = exists(
        select(SubscriptionPeriod.id)
        .join(Subscription)
        .where(
            Subscription.subscriber_user_id == user_id,
            Subscription.creator_id == creator_id,
            SubscriptionPeriod.status.in_(
                [SubscriptionPeriodStatus.active, SubscriptionPeriodStatus.refunded]
            ),
        )
    )
    return await db.scalar(select(ppv | subscription)) is True


async def can_message(db: AsyncSession, sender: User, creator: CreatorProfile) -> bool:
    if sender.id == creator.user_id or await is_blocked(db, sender.id, creator.user_id):
        return sender.id == creator.user_id
    settings = await settings_for_creator(db, creator.id)
    if settings.permission is MessagingPermission.anyone:
        return True
    if settings.permission is MessagingPermission.nobody:
        return False
    if settings.permission is MessagingPermission.followers:
        return (
            await db.scalar(
                select(Follow.id).where(
                    Follow.user_id == sender.id, Follow.creator_id == creator.id
                )
            )
            is not None
        )
    if settings.permission is MessagingPermission.subscribers:
        return await active_subscriber(db, sender.id, creator.id)
    return await previous_customer(db, sender.id, creator.id)


async def resolve_send_price(
    db: AsyncSession, sender: User, creator: CreatorProfile
) -> tuple[int | None, str | None]:
    settings = await settings_for_creator(db, creator.id)
    if not settings.send_fee_minor:
        return None, None
    if settings.subscribers_free and await active_subscriber(db, sender.id, creator.id):
        return None, None
    return settings.send_fee_minor, settings.send_fee_currency


async def conversation_for_pair(
    db: AsyncSession, creator: CreatorProfile, viewer: User
) -> Conversation:
    row = await db.scalar(
        select(Conversation)
        .where(Conversation.creator_id == creator.id, Conversation.viewer_user_id == viewer.id)
        .with_for_update()
    )
    if row:
        return row
    row = Conversation(creator_id=creator.id, viewer_user_id=viewer.id)
    db.add(row)
    await db.flush()
    db.add_all(
        [
            ConversationParticipant(conversation_id=row.id, user_id=viewer.id),
            ConversationParticipant(conversation_id=row.id, user_id=creator.user_id),
        ]
    )
    await db.flush()
    return row


async def assert_participant(
    db: AsyncSession, conversation: Conversation, user: User
) -> CreatorProfile:
    creator = await db.get(CreatorProfile, conversation.creator_id)
    if not creator or user.id not in {creator.user_id, conversation.viewer_user_id}:
        raise PermissionError("Conversation not found")
    return creator


async def send_message(
    db: AsyncSession,
    sender: User,
    creator_id: UUID,
    body: str | None,
    reply_to_message_id: UUID | None = None,
    compliance_decision: ComplianceDecision | None = None,
) -> Message:
    await require_messaging_feature(db, sender, decision=compliance_decision)
    try:
        creator = await require_public_creator_access(db, creator_id, sender.id)
    except ValueError as exc:
        raise PermissionError("Messaging is not permitted") from exc
    if not body or not body.strip():
        raise MessagingError("Message text is required")
    if len(body) > 4000:
        raise MessagingError("Message text is too long")
    if sender.id == creator.user_id:
        raise MessagingError("Creator replies require an existing conversation")
    creator_user = await db.get(User, creator.user_id)
    if creator_user is None:
        raise PermissionError("Messaging is not permitted")
    await require_messaging_feature(db, creator_user)
    if not await can_message(db, sender, creator):
        raise PermissionError("Messaging is not permitted")
    conversation = await conversation_for_pair(db, creator, sender)
    # Replies target an explicit existing conversation, preventing accidental recipient selection.
    message = Message(
        conversation_id=conversation.id,
        sender_user_id=sender.id,
        message_type=MessageType.text,
        body=body.strip(),
        reply_to_message_id=reply_to_message_id,
    )
    db.add(message)
    conversation.last_message_at = datetime.now(UTC)
    await record_event(
        db,
        "message.sent",
        actor_user_id=sender.id,
        target_type="message",
        target_id=str(message.id),
    )
    await emit_transactional(
        db,
        recipient_user_id=creator.user_id,
        notification_type="MESSAGE_RECEIVED",
        source_domain="messaging",
        source_id=str(message.id),
        title="You have a new message",
        body="Open FanBackstage to view your message.",
        target_path="/messages",
    )
    return message


async def send_in_conversation(
    db: AsyncSession,
    sender: User,
    conversation_id: UUID,
    body: str,
    reply_to_message_id: UUID | None = None,
    compliance_decision: ComplianceDecision | None = None,
) -> Message:
    await require_messaging_feature(db, sender, decision=compliance_decision)
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        raise PermissionError("Conversation not found")
    creator = await assert_participant(db, conversation, sender)
    if not body.strip() or len(body) > 4000:
        raise MessagingError("Message text is invalid")
    other = conversation.viewer_user_id if sender.id == creator.user_id else creator.user_id
    recipient = await db.get(User, other)
    if recipient is None:
        raise PermissionError("Conversation not found")
    await require_messaging_feature(db, recipient)
    if await is_blocked(db, sender.id, other):
        raise PermissionError("Messaging is blocked")
    if sender.id != creator.user_id and not await can_message(db, sender, creator):
        raise PermissionError("Messaging is not permitted")
    if reply_to_message_id and not await db.scalar(
        select(Message.id).where(
            Message.id == reply_to_message_id, Message.conversation_id == conversation.id
        )
    ):
        raise MessagingError("Reply target is not in this conversation")
    message = Message(
        conversation_id=conversation.id,
        sender_user_id=sender.id,
        message_type=MessageType.text,
        body=body.strip(),
        reply_to_message_id=reply_to_message_id,
    )
    db.add(message)
    conversation.last_message_at = datetime.now(UTC)
    await record_event(
        db,
        "message.sent",
        actor_user_id=sender.id,
        target_type="message",
        target_id=str(message.id),
    )
    await emit_transactional(
        db,
        recipient_user_id=other,
        notification_type="MESSAGE_RECEIVED",
        source_domain="messaging",
        source_id=str(message.id),
        title="You have a new message",
        body="Open FanBackstage to view your message.",
        target_path="/messages",
    )
    return message


async def mark_read(db: AsyncSession, user: User, conversation_id: UUID) -> None:
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        raise PermissionError("Conversation not found")
    await assert_participant(db, conversation, user)
    participant = await db.scalar(
        select(ConversationParticipant)
        .where(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.user_id == user.id,
        )
        .with_for_update()
    )
    participant.last_read_at = datetime.now(UTC)
    await record_event(
        db,
        "message.read",
        actor_user_id=user.id,
        target_type="conversation",
        target_id=str(conversation_id),
    )


async def set_inbox_state(
    db: AsyncSession,
    user: User,
    conversation_id: UUID,
    *,
    archived: bool | None = None,
    muted: bool | None = None,
) -> Conversation:
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        raise PermissionError("Conversation not found")
    creator = await assert_participant(db, conversation, user)
    suffix = "creator" if user.id == creator.user_id else "viewer"
    if archived is not None:
        setattr(conversation, f"archived_by_{suffix}", archived)
    if muted is not None:
        setattr(conversation, f"muted_by_{suffix}", muted)
    await record_event(
        db,
        "conversation.inbox_state_changed",
        actor_user_id=user.id,
        target_type="conversation",
        target_id=str(conversation.id),
        metadata={"archived": archived, "muted": muted},
    )
    return conversation


async def attach_media(
    db: AsyncSession,
    creator_user: User,
    message_id: UUID,
    asset_id: UUID,
    unlock_price_minor: int | None,
    unlock_currency: str | None,
    compliance_decision: ComplianceDecision | None = None,
) -> MessageAttachment:
    message = await db.get(Message, message_id)
    if not message:
        raise PermissionError("Message not found")
    conversation = await db.get(Conversation, message.conversation_id)
    creator = await assert_participant(db, conversation, creator_user)
    if message.sender_user_id != creator.user_id:
        raise PermissionError("Only the creator can attach media")
    if bool(unlock_price_minor) != bool(unlock_currency):
        raise MessagingError("Locked attachments require a price and currency")
    asset = await db.get(MediaAsset, asset_id)
    if (
        not asset
        or asset.owner_creator_id != creator.id
        or asset.status is not MediaStatus.ready
        or asset.deleted_at is not None
        or asset.moderation_status.name in {"flagged", "rejected", "removed"}
    ):
        raise MessagingError("Only ready creator-owned media may be attached")
    try:
        await require_media_context_available(
            db,
            asset.id,
            context_type="message",
            context_id=message.id,
        )
    except ValueError as exc:
        raise MessagingError(str(exc)) from exc
    adult_restricted = asset.audience is MediaAudience.adult_restricted
    await require_messaging_feature(
        db,
        creator_user,
        adult_restricted=adult_restricted,
        decision=compliance_decision,
    )
    viewer = await db.get(User, conversation.viewer_user_id)
    if viewer is None:
        raise PermissionError("Message recipient not found")
    await require_messaging_feature(db, viewer, adult_restricted=adult_restricted)
    attachment = MessageAttachment(
        message_id=message.id,
        media_asset_id=asset.id,
        unlock_price_minor=unlock_price_minor,
        unlock_currency=currency_code(unlock_currency) if unlock_currency else None,
    )
    db.add(attachment)
    message.message_type = MessageType.media
    await db.flush()
    await record_event(
        db,
        "message.attachment_created",
        actor_user_id=creator_user.id,
        target_type="message_attachment",
        target_id=str(attachment.id),
    )
    return attachment


async def messaging_commission(db: AsyncSession) -> int:
    rule = await db.scalar(
        select(CommissionRule).where(
            CommissionRule.revenue_type == "messaging", CommissionRule.active.is_(True)
        )
    )
    if rule:
        return rule.basis_points
    rule = CommissionRule(
        revenue_type="messaging",
        basis_points=get_settings().finance_default_commission_basis_points,
    )
    db.add(rule)
    await db.flush()
    return rule.basis_points


async def create_unlock_purchase(
    db: AsyncSession,
    buyer: User,
    attachment_id: UUID,
    idempotency_key: str,
    compliance_decisions: dict[ComplianceFeature, ComplianceDecision] | None = None,
) -> MessageUnlockPurchase:
    if not idempotency_key or len(idempotency_key) > 128:
        raise MessagingError("A valid Idempotency-Key is required")
    attachment = await db.get(MessageAttachment, attachment_id)
    if not attachment or not attachment.unlock_price_minor or not attachment.unlock_currency:
        raise MessagingError("Locked attachment not found")
    message = await db.get(Message, attachment.message_id)
    conversation = await db.get(Conversation, message.conversation_id) if message else None
    if not message or not conversation or buyer.id != conversation.viewer_user_id:
        raise PermissionError("Locked attachment not found")
    existing = await db.scalar(
        select(MessageUnlockPurchase)
        .join(PaymentAttempt)
        .where(
            PaymentAttempt.buyer_user_id == buyer.id,
            PaymentAttempt.idempotency_key == idempotency_key,
        )
    )
    if existing:
        return existing
    existing = await db.scalar(
        select(MessageUnlockPurchase).where(
            MessageUnlockPurchase.buyer_user_id == buyer.id,
            MessageUnlockPurchase.message_attachment_id == attachment.id,
        )
    )
    if existing:
        return existing
    try:
        creator = await require_public_creator_access(db, conversation.creator_id, buyer.id)
    except ValueError as exc:
        raise PermissionError("Locked attachment not found") from exc
    asset = await db.get(MediaAsset, attachment.media_asset_id)
    await require_messaging_compliance(
        db,
        buyer,
        adult_restricted=bool(asset is None or asset.audience is MediaAudience.adult_restricted),
        decisions=compliance_decisions,
    )
    currency = currency_code(attachment.unlock_currency)
    bps = await messaging_commission(db)
    fee, creator_amount = commission_amount(attachment.unlock_price_minor, bps)
    attempt = PaymentAttempt(
        buyer_user_id=buyer.id,
        provider=get_settings().payment_provider,
        provider_reference=new_provider_reference(),
        amount_minor=attachment.unlock_price_minor,
        currency=currency,
        idempotency_key=idempotency_key,
    )
    db.add(attempt)
    await db.flush()
    purchase = MessageUnlockPurchase(
        buyer_user_id=buyer.id,
        seller_creator_id=creator.id,
        message_attachment_id=attachment.id,
        payment_attempt_id=attempt.id,
        gross_amount_minor=attachment.unlock_price_minor,
        platform_fee_minor=fee,
        creator_amount_minor=creator_amount,
        commission_basis_points=bps,
        currency=currency,
    )
    db.add(purchase)
    await db.flush()
    return purchase


async def settle_message_unlock(
    db: AsyncSession, purchase: MessageUnlockPurchase
) -> MessageUnlockPurchase:
    """Settle a verified paid-message unlock through the shared ledger only once."""
    if purchase.status == "paid":
        return purchase
    clearing = await _account(db, LedgerAccountKind.platform_clearing, purchase.currency)
    revenue = await _account(db, LedgerAccountKind.platform_revenue, purchase.currency)
    from app.finance.service import creator_revenue_allocation
    from app.referrals.service import record_revenue_allocation, revenue_allocation

    attempt = await db.get(PaymentAttempt, purchase.payment_attempt_id)
    event_at = attempt.completed_at if attempt and attempt.completed_at else purchase.created_at
    allocation_entries, allocation_metadata = await creator_revenue_allocation(
        db,
        purchase.seller_creator_id,
        purchase.currency,
        purchase.creator_amount_minor,
        event_at,
    )
    referral_entries, referral_allocation = await revenue_allocation(
        db,
        buyer_user_id=purchase.buyer_user_id,
        revenue_type="messaging",
        currency=purchase.currency,
        platform_fee_minor=purchase.platform_fee_minor,
        occurred_at=event_at,
    )
    referral_amount = int(referral_allocation["amount_minor"]) if referral_allocation else 0
    ledger = await post_entries(
        db,
        transaction_type=LedgerTransactionType.messaging_charge,
        currency=purchase.currency,
        idempotency_key=f"message_unlock:{purchase.id}",
        reference=f"message_unlock:{purchase.id}",
        entries=[
            (clearing, LedgerDirection.debit, purchase.gross_amount_minor),
            (revenue, LedgerDirection.credit, purchase.platform_fee_minor - referral_amount),
            *referral_entries,
            *allocation_entries,
        ],
        metadata={
            "message_unlock_purchase_id": str(purchase.id),
            "message_attachment_id": str(purchase.message_attachment_id),
            "platform_fee_minor": str(purchase.platform_fee_minor),
            "referral_amount_minor": str(referral_amount),
            **allocation_metadata,
        },
    )
    await record_revenue_allocation(
        db,
        source_ledger_transaction_id=ledger.id,
        allocation=referral_allocation,
    )
    purchase.status, purchase.purchased_at, purchase.ledger_transaction_id = (
        "paid",
        datetime.now(UTC),
        ledger.id,
    )
    await record_event(
        db,
        "message.unlock_settled",
        actor_user_id=purchase.buyer_user_id,
        target_type="message_unlock_purchase",
        target_id=str(purchase.id),
    )
    return purchase


async def initiate_paid_send(
    db: AsyncSession,
    buyer: User,
    creator_id: UUID,
    body: str,
    idempotency_key: str,
    compliance_decisions: dict[ComplianceFeature, ComplianceDecision] | None = None,
) -> PendingMessageSend:
    if not idempotency_key or len(idempotency_key) > 128:
        raise MessagingError("A valid Idempotency-Key is required")
    existing = await db.scalar(
        select(PendingMessageSend)
        .join(PaymentAttempt)
        .where(
            PaymentAttempt.buyer_user_id == buyer.id,
            PaymentAttempt.idempotency_key == idempotency_key,
        )
    )
    if existing:
        return existing
    try:
        creator = await require_public_creator_access(db, creator_id, buyer.id)
    except ValueError as exc:
        raise PermissionError("Messaging is not permitted") from exc
    if not creator or not await can_message(db, buyer, creator):
        raise PermissionError("Messaging is not permitted")
    amount, currency = await resolve_send_price(db, buyer, creator)
    if not amount or not currency:
        raise MessagingError("This message does not require payment")
    await require_messaging_compliance(
        db,
        buyer,
        adult_restricted=True,
        decisions=compliance_decisions,
    )
    bps = await messaging_commission(db)
    fee, creator_amount = commission_amount(amount, bps)
    attempt = PaymentAttempt(
        buyer_user_id=buyer.id,
        provider=get_settings().payment_provider,
        provider_reference=new_provider_reference(),
        amount_minor=amount,
        currency=currency_code(currency),
        idempotency_key=idempotency_key,
    )
    db.add(attempt)
    await db.flush()
    pending = PendingMessageSend(
        buyer_user_id=buyer.id,
        creator_id=creator.id,
        body=body.strip(),
        payment_attempt_id=attempt.id,
        gross_amount_minor=amount,
        platform_fee_minor=fee,
        creator_amount_minor=creator_amount,
        commission_basis_points=bps,
        currency=currency_code(currency),
    )
    db.add(pending)
    await db.flush()
    return pending


async def settle_paid_send(db: AsyncSession, pending: PendingMessageSend) -> PendingMessageSend:
    if pending.status == "paid":
        return pending
    creator = await db.get(CreatorProfile, pending.creator_id)
    conversation = await conversation_for_pair(
        db, creator, await db.get(User, pending.buyer_user_id)
    )
    clearing = await _account(db, LedgerAccountKind.platform_clearing, pending.currency)
    revenue = await _account(db, LedgerAccountKind.platform_revenue, pending.currency)
    from app.finance.service import creator_revenue_allocation
    from app.referrals.service import record_revenue_allocation, revenue_allocation

    attempt = await db.get(PaymentAttempt, pending.payment_attempt_id)
    event_at = attempt.completed_at if attempt and attempt.completed_at else pending.created_at
    allocation_entries, allocation_metadata = await creator_revenue_allocation(
        db,
        pending.creator_id,
        pending.currency,
        pending.creator_amount_minor,
        event_at,
    )
    referral_entries, referral_allocation = await revenue_allocation(
        db,
        buyer_user_id=pending.buyer_user_id,
        revenue_type="messaging",
        currency=pending.currency,
        platform_fee_minor=pending.platform_fee_minor,
        occurred_at=event_at,
    )
    referral_amount = int(referral_allocation["amount_minor"]) if referral_allocation else 0
    ledger = await post_entries(
        db,
        transaction_type=LedgerTransactionType.messaging_charge,
        currency=pending.currency,
        idempotency_key=f"paid_message_send:{pending.id}",
        reference=f"paid_message_send:{pending.id}",
        entries=[
            (clearing, LedgerDirection.debit, pending.gross_amount_minor),
            (revenue, LedgerDirection.credit, pending.platform_fee_minor - referral_amount),
            *referral_entries,
            *allocation_entries,
        ],
        metadata={
            "pending_message_send_id": str(pending.id),
            "platform_fee_minor": str(pending.platform_fee_minor),
            "referral_amount_minor": str(referral_amount),
            **allocation_metadata,
        },
    )
    await record_revenue_allocation(
        db,
        source_ledger_transaction_id=ledger.id,
        allocation=referral_allocation,
    )
    message = Message(
        conversation_id=conversation.id,
        sender_user_id=pending.buyer_user_id,
        message_type=MessageType.text,
        body=pending.body,
    )
    db.add(message)
    await db.flush()
    conversation.last_message_at = datetime.now(UTC)
    pending.status, pending.ledger_transaction_id, pending.message_id = (
        "paid",
        ledger.id,
        message.id,
    )
    return pending


async def campaign_recipients(db: AsyncSession, campaign: MassMessageCampaign) -> list[UUID]:
    creator = await db.get(CreatorProfile, campaign.creator_id)
    if campaign.audience_segment is AudienceSegment.followers:
        query = select(Follow.user_id).where(Follow.creator_id == creator.id)
    elif campaign.audience_segment is AudienceSegment.active_subscribers:
        now = datetime.now(UTC)
        query = select(ContentEntitlement.subject_user_id).where(
            ContentEntitlement.creator_id == creator.id,
            ContentEntitlement.status == EntitlementStatus.active,
            ContentEntitlement.valid_from <= now,
            or_(ContentEntitlement.valid_until.is_(None), ContentEntitlement.valid_until > now),
        )
    elif campaign.audience_segment is AudienceSegment.expired_subscribers:
        active = select(ContentEntitlement.subject_user_id).where(
            ContentEntitlement.creator_id == creator.id,
            ContentEntitlement.status == EntitlementStatus.active,
        )
        query = select(Subscription.subscriber_user_id).where(
            Subscription.creator_id == creator.id,
            Subscription.status == SubscriptionStatus.expired,
            Subscription.subscriber_user_id.not_in(active),
        )
    else:
        from app.models.finance import Purchase, PurchaseStatus

        query = select(Purchase.buyer_user_id).where(
            Purchase.seller_creator_id == creator.id, Purchase.status == PurchaseStatus.paid
        )
    users = set((await db.scalars(query)).all())
    blocked = set(
        (
            await db.scalars(
                select(UserBlock.blocked_user_id).where(
                    UserBlock.blocker_user_id == creator.user_id
                )
            )
        ).all()
    )
    return list(users - blocked - {creator.user_id})


async def snapshot_campaign_recipients(db: AsyncSession, campaign: MassMessageCampaign) -> int:
    """Persist the audience at campaign creation so later retries cannot expand it."""
    existing = await db.scalar(
        select(MassMessageRecipient.id)
        .where(MassMessageRecipient.campaign_id == campaign.id)
        .limit(1)
    )
    if existing:
        return 0
    recipients = await campaign_recipients(db, campaign)
    db.add_all(
        MassMessageRecipient(campaign_id=campaign.id, recipient_user_id=user_id)
        for user_id in recipients
    )
    await db.flush()
    return len(recipients)


async def execute_campaign(db: AsyncSession, campaign_id: UUID) -> int:
    campaign = await db.scalar(
        select(MassMessageCampaign).where(MassMessageCampaign.id == campaign_id).with_for_update()
    )
    if not campaign or campaign.status in {CampaignStatus.completed, CampaignStatus.cancelled}:
        return 0
    creator = await db.get(CreatorProfile, campaign.creator_id)
    creator_user = await db.get(User, creator.user_id) if creator else None
    if creator is None or creator_user is None:
        raise MessagingError("Campaign creator is unavailable")
    await require_messaging_feature(db, creator_user)
    campaign.status, campaign.started_at = (
        CampaignStatus.processing,
        campaign.started_at or datetime.now(UTC),
    )
    count = 0
    recipients = (
        await db.scalars(
            select(MassMessageRecipient)
            .where(MassMessageRecipient.campaign_id == campaign.id)
            .with_for_update()
        )
    ).all()
    for recipient in recipients:
        if recipient.message_id:
            continue
        # A campaign audience is snapshotted, but a later block is always respected.
        if await is_blocked(db, creator.user_id, recipient.recipient_user_id):
            continue
        viewer = await db.get(User, recipient.recipient_user_id)
        if not viewer:
            continue
        try:
            await require_messaging_feature(db, viewer)
        except MessagingError:
            continue
        conversation = await conversation_for_pair(db, creator, viewer)
        message = Message(
            conversation_id=conversation.id,
            sender_user_id=creator.user_id,
            message_type=MessageType.text,
            body=campaign.body,
        )
        db.add(message)
        await db.flush()
        recipient.message_id, recipient.delivered_at = message.id, datetime.now(UTC)
        conversation.last_message_at = datetime.now(UTC)
        count += 1
    campaign.status, campaign.completed_at = CampaignStatus.completed, datetime.now(UTC)
    await record_event(
        db,
        "mass_message.completed",
        actor_user_id=campaign.created_by_user_id,
        target_type="mass_message_campaign",
        target_id=str(campaign.id),
        metadata={"delivered": count},
    )
    return count


async def execute_due_campaigns(db: AsyncSession, limit: int = 25) -> int:
    """Run UTC-scheduled campaigns with row locks; recipient uniqueness makes retries safe."""
    now = datetime.now(UTC)
    rows = (
        await db.scalars(
            select(MassMessageCampaign)
            .where(
                MassMessageCampaign.status == CampaignStatus.scheduled,
                MassMessageCampaign.scheduled_at <= now,
            )
            .order_by(MassMessageCampaign.scheduled_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    delivered = 0
    for campaign in rows:
        delivered += await execute_campaign(db, campaign.id)
    return delivered
