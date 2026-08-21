from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import func, select

from app.api.deps import CurrentIdentity, Db
from app.core.rate_limit import enforce_messaging_rate_limit
from app.messaging import service
from app.models.creator import CreatorProfile
from app.models.messaging import (
    AudienceSegment,
    CampaignStatus,
    Conversation,
    ConversationParticipant,
    MassMessageCampaign,
    Message,
    MessagingPermission,
    UserBlock,
)
from app.schemas.messaging import (
    AttachmentInput,
    CampaignInput,
    ConversationResponse,
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
    creator = await db.get(CreatorProfile, creator_id)
    if not creator or not await service.can_message(db, identity[0], creator):
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
        creator = await db.get(CreatorProfile, creator_id)
        if not creator or identity[0].id == creator.user_id:
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
        result.append(
            ConversationResponse(
                id=row.id,
                creator_id=row.creator_id,
                viewer_user_id=row.viewer_user_id,
                last_message_at=row.last_message_at,
                unread_count=unread,
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
    rows = (
        await db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(min(max(limit, 1), 100))
        )
    ).all()
    return [response(row) for row in rows]


@router.post("/conversations/{conversation_id}/read", status_code=204)
async def read(conversation_id: UUID, identity: CurrentIdentity, db: Db) -> None:
    try:
        await service.mark_read(db, identity[0], conversation_id)
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


@router.post("/campaigns", response_model=dict)
async def campaign(payload: CampaignInput, identity: CurrentIdentity, db: Db) -> dict:
    try:
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
        if not payload.scheduled_at:
            await service.execute_campaign(db, item.id)
        await db.commit()
        return {"id": str(item.id), "status": item.status.value}
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc
