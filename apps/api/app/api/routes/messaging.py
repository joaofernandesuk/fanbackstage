from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.accounts import adult_access
from app.api.deps import CurrentIdentity, Db
from app.content.access import can_access_asset
from app.core.config import get_settings
from app.core.rate_limit import enforce_messaging_rate_limit
from app.creators.service import require_public_creator_access
from app.media.storage import storage_provider
from app.messaging import service
from app.models.content import (
    DerivativeType,
    MediaAsset,
    MediaAudience,
    MediaDerivative,
    MediaStatus,
)
from app.models.creator import CreatorProfile
from app.models.messaging import (
    AudienceSegment,
    CampaignStatus,
    Conversation,
    ConversationParticipant,
    MassMessageCampaign,
    Message,
    MessageAttachment,
    MessageReport,
    MessagingPermission,
    UserBlock,
)
from app.schemas.messaging import (
    AttachmentAccessResponse,
    AttachmentInput,
    CampaignInput,
    ConversationResponse,
    MessageReportInput,
    MessageResponse,
    MessagingSettingsInput,
    SendMessageInput,
)

router = APIRouter(prefix="/messages", tags=["messaging"])


def response(message: Message) -> MessageResponse:
    return MessageResponse(
        id=message.id,
        conversation_id=message.conversation_id,
        sender_user_id=message.sender_user_id,
        body=message.body if message.status.value == "sent" else None,
        status=message.status.value,
        created_at=message.created_at,
    )


@router.put("/settings", response_model=dict)
async def update_settings(
    payload: MessagingSettingsInput, identity: CurrentIdentity, db: Db
) -> dict:
    try:
        creator = await service.creator_for_user(db, identity[0])
        settings = await service.settings_for_creator(db, creator.id)
        settings.permission = MessagingPermission(payload.permission)
        settings.send_fee_minor = payload.send_fee_minor
        settings.send_fee_currency = (
            payload.send_fee_currency.upper() if payload.send_fee_currency else None
        )
        settings.subscribers_free = payload.subscribers_free
        if bool(settings.send_fee_minor) != bool(settings.send_fee_currency):
            raise ValueError("A send-fee currency is required with a send fee")
        await db.commit()
        return {
            "permission": settings.permission.value,
            "send_fee_minor": settings.send_fee_minor,
            "send_fee_currency": settings.send_fee_currency,
            "subscribers_free": settings.subscribers_free,
        }
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.get("/creator/{creator_id}/send-price")
async def send_price(creator_id: UUID, identity: CurrentIdentity, db: Db) -> dict:
    try:
        creator = await require_public_creator_access(db, creator_id, identity[0].id)
    except ValueError as exc:
        raise HTTPException(403, "Messaging is not permitted") from exc
    if not await service.can_message(db, identity[0], creator):
        raise HTTPException(403, "Messaging is not permitted")
    amount, currency = await service.resolve_send_price(db, identity[0], creator)
    return {
        "amount_minor": amount,
        "currency": currency,
        "requires_confirmation": amount is not None,
    }


@router.post("/creator/{creator_id}", response_model=MessageResponse)
async def start(
    creator_id: UUID, payload: SendMessageInput, request: Request, identity: CurrentIdentity, db: Db
) -> MessageResponse:
    try:
        await enforce_messaging_rate_limit(request, str(identity[0].id), "send")
        try:
            creator = await require_public_creator_access(db, creator_id, identity[0].id)
        except ValueError as exc:
            raise PermissionError("Messaging is not permitted") from exc
        if identity[0].id == creator.user_id:
            raise ValueError("Use a conversation to send creator messages")
        # A priced initiation is intentionally not persisted until its existing payment attempt settles.
        amount, _ = await service.resolve_send_price(db, identity[0], creator)
        if amount:
            raise ValueError("This message requires payment; confirm it through the paid-send flow")
        message = await service.send_message(
            db, identity[0], creator_id, payload.body, payload.reply_to_message_id
        )
        await db.commit()
        return response(message)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/conversations/{conversation_id}", response_model=MessageResponse)
async def send(
    conversation_id: UUID,
    payload: SendMessageInput,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
) -> MessageResponse:
    try:
        await enforce_messaging_rate_limit(request, str(identity[0].id), "send")
        message = await service.send_in_conversation(
            db, identity[0], conversation_id, payload.body, payload.reply_to_message_id
        )
        await db.commit()
        return response(message)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/creator/{creator_id}/paid-send")
async def paid_send(
    creator_id: UUID,
    payload: SendMessageInput,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    try:
        await enforce_messaging_rate_limit(request, str(identity[0].id), "paid_send")
        pending = await service.initiate_paid_send(
            db, identity[0], creator_id, payload.body, idempotency_key or ""
        )
        await db.commit()
        return {
            "id": str(pending.id),
            "status": pending.status,
            "amount_minor": pending.gross_amount_minor,
            "currency": pending.currency,
            "payment_attempt_id": str(pending.payment_attempt_id),
        }
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.get("/conversations", response_model=list[ConversationResponse])
async def inbox(identity: CurrentIdentity, db: Db, limit: int = 20) -> list[ConversationResponse]:
    rows = (
        await db.scalars(
            select(Conversation)
            .join(
                ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id
            )
            .where(ConversationParticipant.user_id == identity[0].id)
            .order_by(Conversation.last_message_at.desc().nullslast(), Conversation.id.desc())
            .limit(min(max(limit, 1), 50))
        )
    ).all()
    result = []
    for row in rows:
        participant = await db.scalar(
            select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == row.id,
                ConversationParticipant.user_id == identity[0].id,
            )
        )
        unread = (
            await db.scalar(
                select(func.count())
                .select_from(Message)
                .where(
                    Message.conversation_id == row.id,
                    Message.sender_user_id != identity[0].id,
                    (
                        Message.created_at > participant.last_read_at
                        if participant.last_read_at
                        else True
                    ),
                )
            )
            or 0
        )
        creator = await db.get(CreatorProfile, row.creator_id)
        if not creator:
            continue
        is_creator = bool(creator and creator.user_id == identity[0].id)
        result.append(
            ConversationResponse(
                id=row.id,
                creator_id=row.creator_id,
                viewer_user_id=row.viewer_user_id,
                other_user_id=row.viewer_user_id if is_creator else creator.user_id,
                last_message_at=row.last_message_at,
                unread_count=unread,
                archived=row.archived_by_creator if is_creator else row.archived_by_viewer,
                muted=row.muted_by_creator if is_creator else row.muted_by_viewer,
            )
        )
    return result


@router.get("/conversations/{conversation_id}", response_model=list[MessageResponse])
async def messages(
    conversation_id: UUID, identity: CurrentIdentity, db: Db, limit: int = 50
) -> list[MessageResponse]:
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found")
    try:
        await service.assert_participant(db, conversation, identity[0])
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    if identity[0].id == conversation.viewer_user_id:
        try:
            await require_public_creator_access(db, conversation.creator_id, identity[0].id)
        except ValueError as exc:
            raise HTTPException(404, "Attachment not found") from exc
    rows = (
        await db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(min(max(limit, 1), 100))
        )
    ).all()
    return [response(row) for row in rows]


@router.get("/conversations/{conversation_id}/attachments", response_model=list[dict])
async def conversation_attachments(
    conversation_id: UUID, identity: CurrentIdentity, db: Db
) -> list[dict]:
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        raise HTTPException(404, "Conversation not found")
    try:
        await service.assert_participant(db, conversation, identity[0])
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    rows = (
        await db.execute(
            select(MessageAttachment.id, MessageAttachment.message_id)
            .join(Message, Message.id == MessageAttachment.message_id)
            .where(Message.conversation_id == conversation_id)
        )
    ).all()
    return [{"id": str(row.id), "message_id": str(row.message_id)} for row in rows]


@router.post("/conversations/{conversation_id}/read", status_code=204)
async def read(conversation_id: UUID, identity: CurrentIdentity, db: Db) -> None:
    try:
        await service.mark_read(db, identity[0], conversation_id)
        await db.commit()
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(403, str(exc)) from exc


@router.post("/conversations/{conversation_id}/archive", status_code=204)
async def archive(conversation_id: UUID, identity: CurrentIdentity, db: Db) -> None:
    try:
        await service.set_inbox_state(db, identity[0], conversation_id, archived=True)
        await db.commit()
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(403, str(exc)) from exc


@router.delete("/conversations/{conversation_id}/archive", status_code=204)
async def unarchive(conversation_id: UUID, identity: CurrentIdentity, db: Db) -> None:
    try:
        await service.set_inbox_state(db, identity[0], conversation_id, archived=False)
        await db.commit()
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(403, str(exc)) from exc


@router.post("/conversations/{conversation_id}/mute", status_code=204)
async def mute(conversation_id: UUID, identity: CurrentIdentity, db: Db) -> None:
    try:
        await service.set_inbox_state(db, identity[0], conversation_id, muted=True)
        await db.commit()
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(403, str(exc)) from exc


@router.delete("/conversations/{conversation_id}/mute", status_code=204)
async def unmute(conversation_id: UUID, identity: CurrentIdentity, db: Db) -> None:
    try:
        await service.set_inbox_state(db, identity[0], conversation_id, muted=False)
        await db.commit()
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(403, str(exc)) from exc


@router.post("/attachments/{attachment_id}/unlock")
async def unlock(
    attachment_id: UUID,
    identity: CurrentIdentity,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    try:
        purchase = await service.create_unlock_purchase(
            db, identity[0], attachment_id, idempotency_key or ""
        )
        await db.commit()
        return {
            "id": str(purchase.id),
            "status": purchase.status,
            "payment_attempt_id": str(purchase.payment_attempt_id),
            "amount_minor": purchase.gross_amount_minor,
            "currency": purchase.currency,
        }
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


async def attachment_context(db: Db, attachment_id: UUID, identity: CurrentIdentity):
    attachment = await db.get(MessageAttachment, attachment_id)
    message = await db.get(Message, attachment.message_id) if attachment else None
    conversation = await db.get(Conversation, message.conversation_id) if message else None
    if not attachment or not message or not conversation:
        raise HTTPException(404, "Attachment not found")
    try:
        await service.assert_participant(db, conversation, identity[0])
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    asset = await db.get(MediaAsset, attachment.media_asset_id)
    if (
        not asset
        or asset.status is not MediaStatus.ready
        or asset.deleted_at is not None
        or asset.moderation_status.name in {"flagged", "rejected", "removed"}
    ):
        raise HTTPException(404, "Attachment not found")
    return attachment, asset


@router.get("/attachments/{attachment_id}/access", response_model=AttachmentAccessResponse)
async def attachment_access(
    attachment_id: UUID, identity: CurrentIdentity, db: Db
) -> AttachmentAccessResponse:
    attachment, asset = await attachment_context(db, attachment_id, identity)
    preview_type = (
        DerivativeType.blurred_preview
        if asset.media_type.value == "image"
        else DerivativeType.preview_clip
    )
    preview = await db.scalar(
        select(MediaDerivative.id).where(
            MediaDerivative.media_asset_id == asset.id,
            MediaDerivative.derivative_type == preview_type,
            MediaDerivative.status == MediaStatus.ready,
        )
    )
    full_type = (
        DerivativeType.display if asset.media_type.value == "image" else DerivativeType.playback
    )
    full = await db.scalar(
        select(MediaDerivative.id).where(
            MediaDerivative.media_asset_id == asset.id,
            MediaDerivative.derivative_type == full_type,
            MediaDerivative.status == MediaStatus.ready,
        )
    )
    decision = adult_access.resolve_adult_access(identity[0], None)
    allowed = await can_access_asset(db, asset.id, identity[0], decision)
    preview_allowed = asset.audience is MediaAudience.safe_public or decision.allowed
    return AttachmentAccessResponse(
        id=attachment.id,
        media_type=asset.media_type.value,
        locked=attachment.unlock_price_minor is not None and not allowed,
        amount_minor=attachment.unlock_price_minor,
        currency=attachment.unlock_currency,
        preview_delivery_path=(
            f"/messages/attachments/{attachment.id}/preview"
            if preview and preview_allowed
            else None
        ),
        full_delivery_path=f"/media/derivatives/{full}" if allowed and full else None,
    )


@router.get("/attachments/{attachment_id}/preview")
async def attachment_preview(
    attachment_id: UUID, identity: CurrentIdentity, db: Db
) -> RedirectResponse:
    _attachment, asset = await attachment_context(db, attachment_id, identity)
    preview_type = (
        DerivativeType.blurred_preview
        if asset.media_type.value == "image"
        else DerivativeType.preview_clip
    )
    derivative = await db.scalar(
        select(MediaDerivative).where(
            MediaDerivative.media_asset_id == asset.id,
            MediaDerivative.derivative_type == preview_type,
            MediaDerivative.status == MediaStatus.ready,
        )
    )
    if not derivative:
        raise HTTPException(404, "Attachment preview not found")
    decision = adult_access.resolve_adult_access(identity[0], None)
    if asset.audience is MediaAudience.adult_restricted and not decision.allowed:
        raise HTTPException(404, "Attachment preview not found")
    try:
        ttl = (
            adult_access.restricted_delivery_ttl(decision, get_settings().media_url_ttl_seconds)
            if asset.audience is MediaAudience.adult_restricted
            else get_settings().media_url_ttl_seconds
        )
    except ValueError as exc:
        raise HTTPException(404, "Attachment preview not found") from exc
    return RedirectResponse(
        storage_provider().create_download_url(derivative.storage_key, ttl),
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/messages/{message_id}/attachments")
async def add_attachment(
    message_id: UUID, payload: AttachmentInput, identity: CurrentIdentity, db: Db
) -> dict:
    try:
        attachment = await service.attach_media(
            db,
            identity[0],
            message_id,
            payload.media_asset_id,
            payload.unlock_price_minor,
            payload.unlock_currency,
        )
        await db.commit()
        return {
            "id": str(attachment.id),
            "locked": attachment.unlock_price_minor is not None,
            "amount_minor": attachment.unlock_price_minor,
            "currency": attachment.unlock_currency,
        }
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/block/{user_id}", status_code=204)
async def block(user_id: UUID, identity: CurrentIdentity, db: Db) -> None:
    if user_id == identity[0].id:
        raise HTTPException(400, "You cannot block yourself")
    if not await db.scalar(
        select(UserBlock.id).where(
            UserBlock.blocker_user_id == identity[0].id, UserBlock.blocked_user_id == user_id
        )
    ):
        db.add(UserBlock(blocker_user_id=identity[0].id, blocked_user_id=user_id))
        await db.commit()


@router.delete("/block/{user_id}", status_code=204)
async def unblock(user_id: UUID, identity: CurrentIdentity, db: Db) -> None:
    row = await db.scalar(
        select(UserBlock).where(
            UserBlock.blocker_user_id == identity[0].id, UserBlock.blocked_user_id == user_id
        )
    )
    if row:
        await db.delete(row)
        await db.commit()


@router.post("/messages/{message_id}/report", response_model=dict)
async def report_message(
    message_id: UUID,
    payload: MessageReportInput,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
) -> dict:
    await enforce_messaging_rate_limit(request, str(identity[0].id), "report")
    message = await db.get(Message, message_id)
    conversation = await db.get(Conversation, message.conversation_id) if message else None
    if not message or not conversation:
        raise HTTPException(404, "Message not found")
    try:
        await service.assert_participant(db, conversation, identity[0])
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    existing = await db.scalar(
        select(MessageReport).where(
            MessageReport.reporter_user_id == identity[0].id,
            MessageReport.message_id == message.id,
            MessageReport.reason == payload.reason,
        )
    )
    if not existing:
        db.add(
            MessageReport(
                reporter_user_id=identity[0].id,
                message_id=message.id,
                reason=payload.reason,
                details=payload.details,
            )
        )
        await db.commit()
    return {"reported": True}


@router.post("/campaigns", response_model=dict)
async def campaign(payload: CampaignInput, identity: CurrentIdentity, db: Db) -> dict:
    try:
        if payload.scheduled_at and (
            payload.scheduled_at.tzinfo is None
            or payload.scheduled_at.utcoffset() is None
            or payload.scheduled_at <= datetime.now(UTC)
        ):
            raise ValueError("Campaign schedules must be a future UTC timestamp")
        creator = await service.creator_for_user(db, identity[0])
        item = MassMessageCampaign(
            creator_id=creator.id,
            created_by_user_id=identity[0].id,
            audience_segment=AudienceSegment(payload.audience_segment),
            body=payload.body,
            status=CampaignStatus.scheduled if payload.scheduled_at else CampaignStatus.draft,
            scheduled_at=payload.scheduled_at,
        )
        db.add(item)
        await db.flush()
        await service.snapshot_campaign_recipients(db, item)
        if not payload.scheduled_at:
            await service.execute_campaign(db, item.id)
        await db.commit()
        return {"id": str(item.id), "status": item.status.value}
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc
