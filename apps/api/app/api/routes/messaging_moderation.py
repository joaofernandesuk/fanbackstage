from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db
from app.audit.service import record_event
from app.models.messaging import Message, MessageReport, MessageReportStatus
from app.permissions.policies import Permission, authorize

router = APIRouter(prefix="/admin", tags=["messaging moderation"])


@router.get("/message-reports", response_model=list[dict])
async def message_reports(
    identity: CurrentIdentity, db: Db, status: MessageReportStatus | None = None
) -> list[dict]:
    authorize(identity[0], Permission.MODERATION_ACCESS)
    query = select(MessageReport)
    if status:
        query = query.where(MessageReport.status == status)
    rows = (await db.scalars(query.order_by(MessageReport.created_at))).all()
    return [
        {
            "id": str(row.id),
            "message_id": str(row.message_id),
            "reason": row.reason,
            "details": row.details,
            "status": row.status.value,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.get("/messages/{message_id}", response_model=dict)
async def moderator_message_access(
    message_id: str,
    identity: CurrentIdentity,
    db: Db,
    reason: str = Query(min_length=3, max_length=500),
) -> dict:
    """Return private-message text only for a documented moderation purpose."""
    authorize(identity[0], Permission.MODERATION_ACCESS)
    message = await db.get(Message, message_id)
    if not message:
        raise HTTPException(404, "Message not found")
    await record_event(
        db,
        "message.moderator_accessed",
        actor_user_id=identity[0].id,
        target_type="message",
        target_id=str(message.id),
        metadata={"reason": reason},
    )
    await db.commit()
    return {
        "id": str(message.id),
        "conversation_id": str(message.conversation_id),
        "sender_user_id": str(message.sender_user_id),
        "body": message.body if message.status.value == "sent" else None,
        "status": message.status.value,
        "created_at": message.created_at,
    }
