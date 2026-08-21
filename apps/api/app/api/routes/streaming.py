import logging
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db
from app.core.config import get_settings
from app.core.rate_limit import enforce_streaming_rate_limit
from app.finance.service import currency_code
from app.integrations.streaming import LiveKitStreamingProvider
from app.models.streaming import (
    LiveAccessMode,
    LiveChatMessage,
    LiveReport,
    LiveRoom,
    PrivateSessionMode,
)
from app.permissions.policies import Permission, authorize
from app.schemas.streaming import (
    ChatInput,
    CreatorLiveSettingsInput,
    CreatorLiveSettingsResponse,
    LiveBanInput,
    LiveReportInput,
    LiveRoomResponse,
    LiveStartInput,
    PrivateRequestInput,
    PrivateRequestResponse,
    PrivateSessionResponse,
    ProviderTokenResponse,
)
from app.streaming import service

router = APIRouter(prefix="/live", tags=["streaming"])
logger = logging.getLogger("fanbackstage.streaming")


def room_response(room: LiveRoom) -> LiveRoomResponse:
    return LiveRoomResponse(
        id=room.id,
        public_id=room.public_id,
        creator_id=room.creator_id,
        status=room.status.value,
        access_mode=room.access_mode.value,
        title=room.title,
        description=room.description,
        viewer_count=room.viewer_count,
        started_at=room.started_at,
        ended_at=room.ended_at,
    )


def private_session_response(session) -> PrivateSessionResponse:
    return PrivateSessionResponse(
        id=session.id,
        request_id=session.request_id,
        status=session.status.value,
        mode=session.mode.value,
        per_minute_price_minor=session.per_minute_price_minor,
        minimum_charge_minor=session.minimum_charge_minor,
        currency=session.currency,
        billable_seconds=session.billable_seconds,
        payment_attempt_id=session.payment_attempt_id,
    )


def live_settings_response(settings) -> CreatorLiveSettingsResponse:
    return CreatorLiveSettingsResponse(
        private_sessions_enabled=settings.private_sessions_enabled,
        one_to_one_price_minor=settings.one_to_one_price_minor,
        two_to_one_price_minor=settings.two_to_one_price_minor,
        currency=settings.currency,
        minimum_minutes=settings.minimum_minutes,
        max_authorization_minor=settings.max_authorization_minor,
    )


@router.post("/webhooks/livekit", status_code=204)
async def livekit_webhook(request: Request, db: Db) -> None:
    """Accept only signed raw LiveKit events; the browser never reports presence."""
    body = await request.body()
    try:
        event = LiveKitStreamingProvider().verify_webhook(
            body, request.headers.get("Authorization")
        )
        await service.process_livekit_webhook(db, event)
        await db.commit()
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        # Never log the authorization header or body: either may contain a
        # signed provider credential or private-room metadata.
        logger.warning("livekit_webhook_rejected: %s", exc)
        raise HTTPException(401, str(exc)) from exc


@router.post("/rooms", response_model=LiveRoomResponse)
async def start_room(
    payload: LiveStartInput, request: Request, identity: CurrentIdentity, db: Db
) -> LiveRoomResponse:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "live_start")
        room = await service.start_live(
            db, identity[0], payload.title, LiveAccessMode(payload.access_mode), payload.description
        )
        await db.commit()
        return room_response(room)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/rooms/{room_id}/end", response_model=LiveRoomResponse)
async def end_room(room_id: UUID, identity: CurrentIdentity, db: Db) -> LiveRoomResponse:
    try:
        room = await service.end_live(db, identity[0], room_id)
        await db.commit()
        return room_response(room)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.get("/rooms", response_model=list[LiveRoomResponse])
async def discovery(db: Db) -> list[LiveRoomResponse]:
    return [
        room_response(room)
        for room in (
            await db.scalars(
                select(LiveRoom)
                .where(LiveRoom.status == "live")
                .order_by(LiveRoom.started_at.desc())
            )
        ).all()
    ]


@router.get("/settings", response_model=CreatorLiveSettingsResponse)
async def get_live_settings(identity: CurrentIdentity, db: Db) -> CreatorLiveSettingsResponse:
    creator = await service.approved_creator(db, identity[0])
    return live_settings_response(await service.settings_for_creator(db, creator.id))


@router.patch("/settings", response_model=CreatorLiveSettingsResponse)
async def update_live_settings(
    payload: CreatorLiveSettingsInput, identity: CurrentIdentity, db: Db
) -> CreatorLiveSettingsResponse:
    try:
        creator = await service.approved_creator(db, identity[0])
        settings = await service.settings_for_creator(db, creator.id)
        for field, value in payload.model_dump(exclude_unset=True).items():
            if field == "currency" and value is not None:
                value = currency_code(value)
            setattr(settings, field, value)
        if (
            settings.max_authorization_minor
            < settings.one_to_one_price_minor * settings.minimum_minutes
        ):
            raise ValueError("Authorization cap must cover the 1-to-1 minimum charge")
        if (
            settings.max_authorization_minor
            < settings.two_to_one_price_minor * settings.minimum_minutes
        ):
            raise ValueError("Authorization cap must cover the 2-to-1 minimum charge")
        await db.commit()
        return live_settings_response(settings)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/rooms/{room_id}/join")
async def join_room(room_id: UUID, request: Request, identity: CurrentIdentity, db: Db) -> dict:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "live_join")
        participant = await service.join_live(db, identity[0], room_id)
        await db.commit()
        return {"room_id": str(participant.live_room_id), "role": participant.role.value}
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(403, str(exc)) from exc


@router.post("/rooms/{room_id}/token", response_model=ProviderTokenResponse)
async def room_token(room_id: UUID, request: Request, identity: CurrentIdentity, db: Db) -> dict:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "live_token")
        room, token = await service.issue_live_token(db, identity[0], room_id)
        await db.commit()
        return {
            "room_id": str(room.id),
            "provider_url": get_settings().livekit_url,
            "token": token,
        }
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(403, str(exc)) from exc


@router.post("/private-sessions/{session_id}/token", response_model=ProviderTokenResponse)
async def private_session_token(
    session_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> ProviderTokenResponse:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "private_live_token")
        session, token = await service.issue_private_token(db, identity[0], session_id)
        await db.commit()
        return ProviderTokenResponse(
            room_id=session.id,
            provider_url=get_settings().livekit_url,
            token=token,
        )
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(403, str(exc)) from exc


@router.post("/rooms/{room_id}/chat")
async def chat(
    room_id: UUID, payload: ChatInput, request: Request, identity: CurrentIdentity, db: Db
) -> dict:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "live_chat")
        message = await service.post_chat(db, identity[0], room_id, payload.body)
        await db.commit()
        return {"id": str(message.id), "body": message.body}
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(403, str(exc)) from exc


@router.get("/rooms/{room_id}/chat")
async def chat_history(room_id: UUID, identity: CurrentIdentity, db: Db) -> list[dict]:
    participant = await service.join_live(db, identity[0], room_id)
    if participant.left_at:
        raise HTTPException(403, "Live room is unavailable")
    return [
        {
            "id": str(item.id),
            "body": item.body,
            "sender_user_id": str(item.sender_user_id) if item.sender_user_id else None,
        }
        for item in (
            await db.scalars(
                select(LiveChatMessage)
                .where(LiveChatMessage.live_room_id == room_id)
                .order_by(LiveChatMessage.created_at)
            )
        ).all()
    ]


@router.post("/rooms/{room_id}/ban/{viewer_id}")
async def ban_viewer(
    room_id: UUID,
    viewer_id: UUID,
    payload: LiveBanInput,
    identity: CurrentIdentity,
    db: Db,
) -> dict:
    try:
        ban = await service.ban_live_viewer(db, identity[0], room_id, viewer_id, payload.reason)
        await db.commit()
        return {"id": str(ban.id), "room_id": str(ban.live_room_id), "user_id": str(ban.user_id)}
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(403, str(exc)) from exc


@router.post("/rooms/{room_id}/reports")
async def report_room(
    room_id: UUID, payload: LiveReportInput, identity: CurrentIdentity, db: Db
) -> dict:
    try:
        report = await service.report_live(
            db, identity[0], room_id, payload.reason, payload.details, payload.chat_message_id
        )
        await db.commit()
        return {"id": str(report.id), "status": report.status.value}
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/rooms/{room_id}/recording")
async def request_recording(room_id: UUID, identity: CurrentIdentity, db: Db) -> dict:
    try:
        recording = await service.request_public_recording(db, identity[0], room_id)
        await db.commit()
        return {"id": str(recording.id), "status": recording.status.value}
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(403, str(exc)) from exc


@router.get("/admin/reports")
async def live_reports(identity: CurrentIdentity, db: Db) -> list[dict]:
    authorize(identity[0], Permission.MODERATION_ACCESS)
    reports = (await db.scalars(select(LiveReport).order_by(LiveReport.created_at.desc()))).all()
    return [
        {
            "id": str(report.id),
            "room_id": str(report.live_room_id),
            "reason": report.reason,
            "status": report.status.value,
        }
        for report in reports
    ]


@router.get("/admin/reports/{report_id}/context")
async def live_report_context(
    report_id: UUID,
    identity: CurrentIdentity,
    db: Db,
    reason: str = Query(min_length=3, max_length=500),
) -> dict:
    authorize(identity[0], Permission.MODERATION_ACCESS)
    try:
        result = await service.moderator_live_report_context(db, identity[0], report_id, reason)
        await db.commit()
        return result
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(404, str(exc)) from exc


@router.post("/creators/{creator_id}/private-requests", response_model=PrivateRequestResponse)
async def request_private(
    creator_id: UUID,
    payload: PrivateRequestInput,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
) -> PrivateRequestResponse:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "private_request")
        item = await service.request_private_session(
            db,
            identity[0],
            creator_id,
            PrivateSessionMode(payload.mode),
            payload.invited_user_id,
            payload.note,
        )
        await db.commit()
        return PrivateRequestResponse(
            id=item.id,
            creator_id=item.creator_id,
            status=item.status.value,
            mode=item.mode.value,
            per_minute_price_minor=item.per_minute_price_minor,
            minimum_charge_minor=item.minimum_charge_minor,
            currency=item.currency,
            expires_at=item.expires_at,
        )
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.get("/private-requests/mine/creator", response_model=list[PrivateRequestResponse])
async def creator_private_requests(
    identity: CurrentIdentity, db: Db
) -> list[PrivateRequestResponse]:
    try:
        rows = await service.creator_pending_private_requests(db, identity[0])
        return [
            PrivateRequestResponse(
                id=item.id,
                creator_id=item.creator_id,
                status=item.status.value,
                mode=item.mode.value,
                per_minute_price_minor=item.per_minute_price_minor,
                minimum_charge_minor=item.minimum_charge_minor,
                currency=item.currency,
                expires_at=item.expires_at,
            )
            for item in rows
        ]
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/private-sessions/mine", response_model=list[PrivateSessionResponse])
async def my_private_sessions(identity: CurrentIdentity, db: Db) -> list[PrivateSessionResponse]:
    return [
        private_session_response(session)
        for session in await service.participant_private_sessions(db, identity[0])
    ]


@router.post("/private-requests/{request_id}/accept", response_model=PrivateSessionResponse)
async def accept_private(
    request_id: UUID, identity: CurrentIdentity, db: Db
) -> PrivateSessionResponse:
    try:
        session = await service.accept_private_request(db, identity[0], request_id)
        await db.commit()
        return private_session_response(session)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/private-sessions/{session_id}/end", response_model=PrivateSessionResponse)
async def end_private(
    session_id: UUID, identity: CurrentIdentity, db: Db
) -> PrivateSessionResponse:
    try:
        session = await service.end_private_session(
            db, identity[0], session_id, "ended_by_participant"
        )
        await db.commit()
        return private_session_response(session)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc
