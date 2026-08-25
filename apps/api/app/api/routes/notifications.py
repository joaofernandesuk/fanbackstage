from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select, update

from app.api.deps import CurrentIdentity, Db
from app.core.config import get_settings
from app.models.notification import InAppNotification, NotificationPreference
from app.notifications.service import _now, mark_provider_event, unsubscribe, update_preference
from app.schemas.auth import MessageResponse
from app.schemas.notification import (
    NotificationPage,
    NotificationResponse,
    PreferenceInput,
    PreferenceResponse,
    ProviderWebhookInput,
)

router = APIRouter(prefix="/notifications", tags=["notifications"])


def preference_response(row: NotificationPreference) -> PreferenceResponse:
    return PreferenceResponse(
        category=row.category,
        email_enabled=row.email_enabled,
        in_app_enabled=row.in_app_enabled,
        consented_at=row.consented_at,
    )


@router.get("", response_model=NotificationPage)
async def list_notifications(
    identity: CurrentIdentity, db: Db, limit: int = 25, offset: int = 0
) -> NotificationPage:
    user, _ = identity
    rows = (
        await db.scalars(
            select(InAppNotification)
            .where(InAppNotification.recipient_user_id == user.id)
            .order_by(InAppNotification.created_at.desc())
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 100))
        )
    ).all()
    unread = await db.scalar(
        select(func.count(InAppNotification.id)).where(
            InAppNotification.recipient_user_id == user.id, InAppNotification.read_at.is_(None)
        )
    )
    return NotificationPage(
        items=[
            NotificationResponse(
                id=row.id,
                notification_type=row.notification_type,
                title=row.title,
                body=row.body,
                target_path=row.target_path,
                created_at=row.created_at,
                read_at=row.read_at,
            )
            for row in rows
        ],
        unread_count=unread or 0,
    )


@router.post("/{notification_id}/read", response_model=MessageResponse)
async def mark_read(notification_id: UUID, identity: CurrentIdentity, db: Db) -> MessageResponse:
    row = await db.scalar(
        select(InAppNotification).where(
            InAppNotification.id == notification_id,
            InAppNotification.recipient_user_id == identity[0].id,
        )
    )
    if not row:
        raise HTTPException(status_code=404, detail="Notification not found")
    row.read_at = row.read_at or _now()
    await db.commit()
    return MessageResponse(message="Notification marked read")


@router.post("/read-all", response_model=MessageResponse)
async def mark_all_read(identity: CurrentIdentity, db: Db) -> MessageResponse:
    await db.execute(
        update(InAppNotification)
        .where(
            InAppNotification.recipient_user_id == identity[0].id,
            InAppNotification.read_at.is_(None),
        )
        .values(read_at=_now())
    )
    await db.commit()
    return MessageResponse(message="Notifications marked read")


@router.get("/preferences", response_model=list[PreferenceResponse])
async def preferences(identity: CurrentIdentity, db: Db) -> list[PreferenceResponse]:
    rows = (
        await db.scalars(
            select(NotificationPreference)
            .where(NotificationPreference.user_id == identity[0].id)
            .order_by(NotificationPreference.category)
        )
    ).all()
    return [preference_response(row) for row in rows]


@router.put("/preferences/{category}", response_model=PreferenceResponse)
async def set_preference(
    category: str, payload: PreferenceInput, identity: CurrentIdentity, db: Db
) -> PreferenceResponse:
    try:
        row = await update_preference(
            db,
            identity[0],
            category,
            payload.email_enabled,
            payload.in_app_enabled,
            payload.consent,
        )
        await db.commit()
        return preference_response(row)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/unsubscribe", response_model=MessageResponse)
async def unsubscribe_marketing(identity: CurrentIdentity, db: Db) -> MessageResponse:
    await unsubscribe(db, identity[0])
    await db.commit()
    return MessageResponse(message="Marketing email unsubscribed")


@router.post("/provider-events", response_model=MessageResponse, include_in_schema=False)
async def provider_event(
    payload: ProviderWebhookInput, request: Request, db: Db
) -> MessageResponse:
    if (
        request.headers.get("X-FanBackstage-Provider-Secret")
        != get_settings().notification_webhook_secret
    ):
        raise HTTPException(status_code=401, detail="Invalid provider webhook")
    if not await mark_provider_event(db, payload.provider_message_id, payload.event):
        raise HTTPException(status_code=404, detail="Unknown provider message")
    await db.commit()
    return MessageResponse(message="Provider event accepted")
