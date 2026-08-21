from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db
from app.core.rate_limit import enforce_streaming_rate_limit
from app.models.streaming import LiveAccessMode, LiveChatMessage, LiveRoom, PrivateSessionMode
from app.schemas.streaming import (
    ChatInput,
    LiveRoomResponse,
    LiveStartInput,
    PrivateRequestInput,
    PrivateRequestResponse,
    PrivateSessionResponse,
)
from app.streaming import service

router = APIRouter(prefix="/live", tags=["streaming"])


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


@router.post("/rooms/{room_id}/token")
async def room_token(room_id: UUID, request: Request, identity: CurrentIdentity, db: Db) -> dict:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "live_token")
        room, token = await service.issue_live_token(db, identity[0], room_id)
        await db.commit()
        return {"room_id": str(room.id), "provider_url": "livekit", "token": token}
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


@router.post("/private-requests/{request_id}/accept", response_model=PrivateSessionResponse)
async def accept_private(
    request_id: UUID, identity: CurrentIdentity, db: Db
) -> PrivateSessionResponse:
    try:
        session = await service.accept_private_request(db, identity[0], request_id)
        await db.commit()
        return PrivateSessionResponse(
            id=session.id,
            request_id=session.request_id,
            status=session.status.value,
            mode=session.mode.value,
            per_minute_price_minor=session.per_minute_price_minor,
            minimum_charge_minor=session.minimum_charge_minor,
            currency=session.currency,
            billable_seconds=session.billable_seconds,
        )
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc
