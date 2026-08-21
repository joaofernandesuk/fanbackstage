"""Streaming domain state. LiveKit transports media; PostgreSQL owns product truth."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.core.config import get_settings
from app.finance import service as finance
from app.finance.service import currency_code, ppv_commission
from app.integrations.streaming import LiveKitStreamingProvider
from app.media.service import approved_creator
from app.models.creator import CreatorProfile
from app.models.finance import (
    LedgerAccountKind,
    LedgerDirection,
    LedgerTransactionType,
    PaymentAttempt,
    PaymentStatus,
)
from app.models.identity import User
from app.models.messaging import UserBlock
from app.models.streaming import (
    CreatorLiveSettings,
    LiveAccessMode,
    LiveBan,
    LiveChatKind,
    LiveChatMessage,
    LiveParticipant,
    LiveParticipantRole,
    LiveRecording,
    LiveRecordingStatus,
    LiveReport,
    LiveRoom,
    LiveRoomStatus,
    PrivateRequestStatus,
    PrivateSession,
    PrivateSessionMode,
    PrivateSessionRequest,
    PrivateSessionStatus,
    ProviderLiveEvent,
    SessionParticipant,
    SessionParticipantRole,
)


class StreamingError(ValueError):
    pass


def _opaque(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"


async def is_blocked(db: AsyncSession, first_user_id: UUID, second_user_id: UUID) -> bool:
    return bool(
        await db.scalar(
            select(
                exists().where(
                    (
                        (UserBlock.blocker_user_id == first_user_id)
                        & (UserBlock.blocked_user_id == second_user_id)
                    )
                    | (
                        (UserBlock.blocker_user_id == second_user_id)
                        & (UserBlock.blocked_user_id == first_user_id)
                    )
                )
            )
        )
    )


async def settings_for_creator(db: AsyncSession, creator_id: UUID) -> CreatorLiveSettings:
    settings = await db.scalar(
        select(CreatorLiveSettings).where(CreatorLiveSettings.creator_id == creator_id)
    )
    if settings is None:
        settings = CreatorLiveSettings(creator_id=creator_id)
        db.add(settings)
        await db.flush()
    return settings


async def start_live(
    db: AsyncSession,
    actor: User,
    title: str,
    access_mode: LiveAccessMode,
    description: str | None = None,
) -> LiveRoom:
    creator = await approved_creator(db, actor)
    active = await db.scalar(
        select(LiveRoom)
        .where(
            LiveRoom.creator_id == creator.id,
            LiveRoom.status.in_(
                [LiveRoomStatus.starting, LiveRoomStatus.live, LiveRoomStatus.ending]
            ),
        )
        .with_for_update()
    )
    if active:
        raise StreamingError("Creator already has an active public live room")
    room = LiveRoom(
        creator_id=creator.id,
        public_id=_opaque("live"),
        provider_room_name=_opaque("lk"),
        status=LiveRoomStatus.live,
        access_mode=access_mode,
        title=title.strip(),
        description=description.strip() if description else None,
        started_at=datetime.now(UTC),
    )
    db.add(room)
    await db.flush()
    db.add(
        LiveParticipant(
            live_room_id=room.id,
            user_id=actor.id,
            role=LiveParticipantRole.creator,
            joined_at=datetime.now(UTC),
        )
    )
    await record_event(
        db, "live.started", actor_user_id=actor.id, target_type="live_room", target_id=str(room.id)
    )
    return room


async def end_live(db: AsyncSession, actor: User, room_id: UUID) -> LiveRoom:
    room = await db.scalar(select(LiveRoom).where(LiveRoom.id == room_id).with_for_update())
    if room is None or room.creator_id != (await approved_creator(db, actor)).id:
        raise PermissionError("Live room not found")
    if room.status is LiveRoomStatus.ended:
        return room
    if room.status is not LiveRoomStatus.live:
        raise StreamingError("Live room cannot be ended from its current state")
    room.status = LiveRoomStatus.ended
    room.ended_at = datetime.now(UTC)
    await record_event(
        db, "live.ended", actor_user_id=actor.id, target_type="live_room", target_id=str(room.id)
    )
    return room


async def can_join_live(db: AsyncSession, viewer: User, room: LiveRoom) -> bool:
    if room.status is not LiveRoomStatus.live:
        return False
    creator = await db.get(CreatorProfile, room.creator_id)
    if creator is None or await is_blocked(db, viewer.id, creator.user_id):
        return False
    if room.access_mode is LiveAccessMode.public:
        return True
    from app.messaging.service import is_active_subscriber
    from app.models.social import Follow

    if room.access_mode is LiveAccessMode.followers:
        return bool(
            await db.scalar(
                select(Follow).where(
                    Follow.creator_id == room.creator_id, Follow.user_id == viewer.id
                )
            )
        )
    return await is_active_subscriber(db, viewer.id, room.creator_id)


async def join_live(db: AsyncSession, viewer: User, room_id: UUID) -> LiveParticipant:
    room = await db.get(LiveRoom, room_id)
    if room is None or not await can_join_live(db, viewer, room):
        raise PermissionError("Live room is unavailable")
    if await db.scalar(
        select(LiveBan).where(LiveBan.live_room_id == room.id, LiveBan.user_id == viewer.id)
    ):
        raise PermissionError("You are banned from this live room")
    participant = await db.scalar(
        select(LiveParticipant)
        .where(LiveParticipant.live_room_id == room.id, LiveParticipant.user_id == viewer.id)
        .with_for_update()
    )
    if participant is None:
        participant = LiveParticipant(
            live_room_id=room.id,
            user_id=viewer.id,
            role=LiveParticipantRole.viewer,
            joined_at=datetime.now(UTC),
        )
        db.add(participant)
        room.viewer_count += 1
        room.peak_viewer_count = max(room.peak_viewer_count, room.viewer_count)
    elif participant.left_at:
        participant.left_at = None
        participant.joined_at = datetime.now(UTC)
        room.viewer_count += 1
        room.peak_viewer_count = max(room.peak_viewer_count, room.viewer_count)
    return participant


async def issue_live_token(db: AsyncSession, viewer: User, room_id: UUID) -> tuple[LiveRoom, str]:
    room = await db.get(LiveRoom, room_id)
    if room is None:
        raise PermissionError("Live room not found")
    participant = await join_live(db, viewer, room_id)
    creator = await db.get(CreatorProfile, room.creator_id)
    can_publish = bool(creator and creator.user_id == viewer.id)
    token = await LiveKitStreamingProvider().participant_token(
        room.provider_room_name, str(viewer.id), can_publish=can_publish, can_subscribe=True
    )
    participant.left_at = None
    return room, token


async def post_chat(db: AsyncSession, actor: User, room_id: UUID, body: str) -> LiveChatMessage:
    participant = await db.scalar(
        select(LiveParticipant).where(
            LiveParticipant.live_room_id == room_id,
            LiveParticipant.user_id == actor.id,
            LiveParticipant.left_at.is_(None),
        )
    )
    if participant is None or not body.strip():
        raise PermissionError("Live chat requires active room membership")
    message = LiveChatMessage(
        live_room_id=room_id, sender_user_id=actor.id, kind=LiveChatKind.text, body=body.strip()
    )
    db.add(message)
    await db.flush()
    return message


async def ban_live_viewer(
    db: AsyncSession, actor: User, room_id: UUID, viewer_id: UUID, reason: str
) -> LiveBan:
    room = await db.scalar(select(LiveRoom).where(LiveRoom.id == room_id).with_for_update())
    creator = await approved_creator(db, actor)
    if room is None or room.creator_id != creator.id or viewer_id == actor.id:
        raise PermissionError("Live room not found")
    ban = await db.scalar(
        select(LiveBan)
        .where(LiveBan.live_room_id == room_id, LiveBan.user_id == viewer_id)
        .with_for_update()
    )
    if ban is None:
        ban = LiveBan(
            live_room_id=room_id, user_id=viewer_id, actor_user_id=actor.id, reason=reason
        )
        db.add(ban)
        participant = await db.scalar(
            select(LiveParticipant).where(
                LiveParticipant.live_room_id == room_id, LiveParticipant.user_id == viewer_id
            )
        )
        if participant and participant.left_at is None:
            participant.left_at = datetime.now(UTC)
            room.viewer_count = max(0, room.viewer_count - 1)
        await record_event(
            db,
            "live.viewer_banned",
            actor_user_id=actor.id,
            target_type="live_room",
            target_id=str(room_id),
            metadata={"viewer_user_id": str(viewer_id), "reason": reason},
        )
    return ban


async def report_live(
    db: AsyncSession,
    actor: User,
    room_id: UUID,
    reason: str,
    details: str | None = None,
    chat_message_id: UUID | None = None,
) -> LiveReport:
    room = await db.get(LiveRoom, room_id)
    if room is None:
        raise PermissionError("Live room not found")
    if chat_message_id and not await db.scalar(
        select(LiveChatMessage).where(
            LiveChatMessage.id == chat_message_id, LiveChatMessage.live_room_id == room_id
        )
    ):
        raise ValueError("Live chat message does not belong to this room")
    report = await db.scalar(
        select(LiveReport).where(
            LiveReport.reporter_user_id == actor.id,
            LiveReport.live_room_id == room_id,
            LiveReport.live_chat_message_id == chat_message_id,
            LiveReport.reason == reason.strip(),
        )
    )
    if report is None:
        report = LiveReport(
            reporter_user_id=actor.id,
            live_room_id=room_id,
            live_chat_message_id=chat_message_id,
            reason=reason.strip(),
            details=details.strip() if details else None,
        )
        db.add(report)
        await db.flush()
        await record_event(
            db,
            "live.reported",
            actor_user_id=actor.id,
            target_type="live_room",
            target_id=str(room_id),
            metadata={"chat_message_id": str(chat_message_id) if chat_message_id else None},
        )
    return report


async def moderator_live_report_context(
    db: AsyncSession, actor: User, report_id: UUID, reason: str
) -> dict:
    report = await db.get(LiveReport, report_id)
    if report is None:
        raise PermissionError("Live report not found")
    message = (
        await db.get(LiveChatMessage, report.live_chat_message_id)
        if report.live_chat_message_id
        else None
    )
    await record_event(
        db,
        "live_report.moderator_accessed",
        actor_user_id=actor.id,
        target_type="live_report",
        target_id=str(report.id),
        metadata={"reason": reason},
    )
    return {
        "id": str(report.id),
        "room_id": str(report.live_room_id),
        "reason": report.reason,
        "details": report.details,
        "status": report.status.value,
        "chat": {"id": str(message.id), "body": message.body} if message else None,
    }


async def request_public_recording(db: AsyncSession, actor: User, room_id: UUID) -> LiveRecording:
    room = await db.scalar(select(LiveRoom).where(LiveRoom.id == room_id).with_for_update())
    creator = await approved_creator(db, actor)
    if (
        room is None
        or room.creator_id != creator.id
        or room.access_mode is not LiveAccessMode.public
    ):
        raise PermissionError("Only an owned public live room can be recorded")
    recording = await db.scalar(
        select(LiveRecording).where(LiveRecording.live_room_id == room.id).with_for_update()
    )
    if recording is None:
        # Egress execution remains a worker/provider concern. Private sessions
        # intentionally have no recording command or model association.
        recording = LiveRecording(
            live_room_id=room.id,
            status=LiveRecordingStatus.requested,
        )
        db.add(recording)
        await db.flush()
        await record_event(
            db,
            "live.recording_requested",
            actor_user_id=actor.id,
            target_type="live_room",
            target_id=str(room.id),
        )
    return recording


async def request_private_session(
    db: AsyncSession,
    requester: User,
    creator_id: UUID,
    mode: PrivateSessionMode,
    invited_user_id: UUID | None = None,
    note: str | None = None,
) -> PrivateSessionRequest:
    creator = await db.get(CreatorProfile, creator_id)
    if (
        creator is None
        or creator.user_id == requester.id
        or await is_blocked(db, requester.id, creator.user_id)
    ):
        raise PermissionError("Private session is unavailable")
    settings = await settings_for_creator(db, creator.id)
    if not settings.private_sessions_enabled:
        raise StreamingError("Private sessions are disabled")
    if mode is PrivateSessionMode.two_to_one and (
        not invited_user_id or invited_user_id == requester.id
    ):
        raise StreamingError("A specific second viewer is required for a 2-to-1 session")
    if mode is PrivateSessionMode.one_to_one and invited_user_id:
        raise StreamingError("A 1-to-1 session cannot include an invited viewer")
    rate = (
        settings.one_to_one_price_minor
        if mode is PrivateSessionMode.one_to_one
        else settings.two_to_one_price_minor
    )
    minimum = rate * settings.minimum_minutes
    request = PrivateSessionRequest(
        creator_id=creator.id,
        requester_user_id=requester.id,
        invited_user_id=invited_user_id,
        mode=mode,
        per_minute_price_minor=rate,
        minimum_minutes=settings.minimum_minutes,
        minimum_charge_minor=minimum,
        max_authorization_minor=settings.max_authorization_minor,
        commission_basis_points=await ppv_commission(db),
        currency=currency_code(settings.currency),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        note=note.strip() if note else None,
    )
    db.add(request)
    await db.flush()
    await record_event(
        db,
        "private_session.requested",
        actor_user_id=requester.id,
        target_type="private_session_request",
        target_id=str(request.id),
    )
    return request


async def creator_pending_private_requests(
    db: AsyncSession, actor: User
) -> list[PrivateSessionRequest]:
    creator = await approved_creator(db, actor)
    return (
        await db.scalars(
            select(PrivateSessionRequest)
            .where(
                PrivateSessionRequest.creator_id == creator.id,
                PrivateSessionRequest.status == PrivateRequestStatus.pending,
                PrivateSessionRequest.expires_at > datetime.now(UTC),
            )
            .order_by(PrivateSessionRequest.created_at)
        )
    ).all()


async def participant_private_sessions(db: AsyncSession, actor: User) -> list[PrivateSession]:
    return (
        await db.scalars(
            select(PrivateSession)
            .join(SessionParticipant, SessionParticipant.private_session_id == PrivateSession.id)
            .where(
                SessionParticipant.user_id == actor.id,
                PrivateSession.status.in_(
                    [
                        PrivateSessionStatus.awaiting_payment_authorization,
                        PrivateSessionStatus.ready,
                        PrivateSessionStatus.connecting,
                        PrivateSessionStatus.active,
                        PrivateSessionStatus.reconnecting,
                    ]
                ),
            )
            .order_by(PrivateSession.created_at.desc())
        )
    ).all()


async def accept_private_request(db: AsyncSession, actor: User, request_id: UUID) -> PrivateSession:
    creator = await approved_creator(db, actor)
    request = await db.scalar(
        select(PrivateSessionRequest)
        .where(PrivateSessionRequest.id == request_id)
        .with_for_update()
    )
    if request is None or request.creator_id != creator.id:
        raise PermissionError("Private session request not found")
    if request.status is not PrivateRequestStatus.pending or request.expires_at <= datetime.now(
        UTC
    ):
        raise StreamingError("Private session request is not pending")
    public_room = await db.scalar(
        select(LiveRoom)
        .where(
            LiveRoom.creator_id == creator.id,
            LiveRoom.status.in_(
                [LiveRoomStatus.starting, LiveRoomStatus.live, LiveRoomStatus.ending]
            ),
        )
        .with_for_update()
    )
    if public_room:
        raise StreamingError("End the public live before accepting a private session request")
    active = await db.scalar(
        select(PrivateSession)
        .where(
            PrivateSession.creator_id == creator.id,
            PrivateSession.status.in_(
                [
                    PrivateSessionStatus.awaiting_payment_authorization,
                    PrivateSessionStatus.ready,
                    PrivateSessionStatus.connecting,
                    PrivateSessionStatus.active,
                    PrivateSessionStatus.reconnecting,
                ]
            ),
        )
        .with_for_update()
    )
    if active:
        raise StreamingError("Creator already has an active private session")
    request.status = PrivateRequestStatus.accepted
    request.accepted_at = datetime.now(UTC)
    session = PrivateSession(
        request_id=request.id,
        creator_id=creator.id,
        payer_user_id=request.requester_user_id,
        mode=request.mode,
        provider_room_name=_opaque("private"),
        per_minute_price_minor=request.per_minute_price_minor,
        minimum_minutes=request.minimum_minutes,
        minimum_charge_minor=request.minimum_charge_minor,
        max_authorization_minor=request.max_authorization_minor,
        commission_basis_points=request.commission_basis_points,
        currency=request.currency,
        accepted_at=request.accepted_at,
    )
    db.add(session)
    await db.flush()
    attempt = PaymentAttempt(
        buyer_user_id=request.requester_user_id,
        provider=get_settings().payment_provider,
        provider_reference=f"devpay_{secrets.token_urlsafe(18)}",
        amount_minor=request.max_authorization_minor,
        currency=request.currency,
        idempotency_key=f"private_session:{request.id}",
    )
    db.add(attempt)
    await db.flush()
    session.payment_attempt_id = attempt.id
    db.add_all(
        [
            SessionParticipant(
                private_session_id=session.id,
                user_id=creator.user_id,
                role=SessionParticipantRole.creator,
            ),
            *(
                [
                    SessionParticipant(
                        private_session_id=session.id,
                        user_id=request.invited_user_id,
                        role=SessionParticipantRole.invited_viewer,
                    )
                ]
                if request.invited_user_id
                else []
            ),
            SessionParticipant(
                private_session_id=session.id,
                user_id=request.requester_user_id,
                role=SessionParticipantRole.payer,
            ),
        ]
    )
    await record_event(
        db,
        "private_session.accepted",
        actor_user_id=actor.id,
        target_type="private_session",
        target_id=str(session.id),
    )
    return session


async def authorize_private_session(db: AsyncSession, session: PrivateSession) -> PrivateSession:
    if session.status is not PrivateSessionStatus.awaiting_payment_authorization:
        return session
    attempt = await db.get(PaymentAttempt, session.payment_attempt_id)
    if attempt is None or attempt.status is not PaymentStatus.succeeded:
        raise StreamingError("Private-session payment authorization is not verified")
    session.status, session.ready_at = PrivateSessionStatus.ready, datetime.now(UTC)
    await record_event(
        db,
        "private_session.authorized",
        actor_user_id=session.payer_user_id,
        target_type="private_session",
        target_id=str(session.id),
    )
    return session


async def private_participant_connected(
    db: AsyncSession, actor: User, session_id: UUID, now: datetime | None = None
) -> PrivateSession:
    now = now or datetime.now(UTC)
    session = await db.scalar(
        select(PrivateSession).where(PrivateSession.id == session_id).with_for_update()
    )
    if session is None or session.status not in (
        PrivateSessionStatus.ready,
        PrivateSessionStatus.connecting,
        PrivateSessionStatus.reconnecting,
    ):
        raise PermissionError("Private session is unavailable")
    participant = await db.scalar(
        select(SessionParticipant)
        .where(
            SessionParticipant.private_session_id == session.id,
            SessionParticipant.user_id == actor.id,
        )
        .with_for_update()
    )
    if participant is None:
        raise PermissionError("You are not invited to this private session")
    participant.joined_at, participant.left_at = now, None
    required = await db.scalars(
        select(SessionParticipant).where(SessionParticipant.private_session_id == session.id)
    )
    if all(item.joined_at and item.left_at is None for item in required):
        session.status, session.active_started_at, session.last_heartbeat_at = (
            PrivateSessionStatus.active,
            now,
            now,
        )
    else:
        session.status = PrivateSessionStatus.connecting
    return session


async def private_participant_disconnected(
    db: AsyncSession, actor: User, session_id: UUID, now: datetime | None = None
) -> PrivateSession:
    now = now or datetime.now(UTC)
    session = await db.scalar(
        select(PrivateSession).where(PrivateSession.id == session_id).with_for_update()
    )
    if session is None:
        raise PermissionError("Private session is unavailable")
    participant = await db.scalar(
        select(SessionParticipant)
        .where(
            SessionParticipant.private_session_id == session.id,
            SessionParticipant.user_id == actor.id,
        )
        .with_for_update()
    )
    if participant is None:
        raise PermissionError("You are not a private-session participant")
    if participant.left_at:
        return session
    if session.status is PrivateSessionStatus.active and session.active_started_at:
        session.billable_seconds += max(0, int((now - session.active_started_at).total_seconds()))
    participant.left_at, session.disconnected_at, session.status = (
        now,
        now,
        PrivateSessionStatus.reconnecting,
    )
    return session


async def issue_private_token(
    db: AsyncSession, actor: User, session_id: UUID
) -> tuple[PrivateSession, str]:
    """Issue a short-lived token only to a named, authorized participant."""
    session = await db.scalar(
        select(PrivateSession).where(PrivateSession.id == session_id).with_for_update()
    )
    if session is None or session.status not in (
        PrivateSessionStatus.ready,
        PrivateSessionStatus.connecting,
        PrivateSessionStatus.active,
        PrivateSessionStatus.reconnecting,
    ):
        raise PermissionError("Private session is unavailable")
    participant = await db.scalar(
        select(SessionParticipant).where(
            SessionParticipant.private_session_id == session.id,
            SessionParticipant.user_id == actor.id,
        )
    )
    if participant is None:
        raise PermissionError("You are not invited to this private session")
    token = await LiveKitStreamingProvider().participant_token(
        session.provider_room_name, str(actor.id), can_publish=True, can_subscribe=True
    )
    return session, token


async def end_private_session(
    db: AsyncSession, actor: User | None, session_id: UUID, reason: str, now: datetime | None = None
) -> PrivateSession:
    now = now or datetime.now(UTC)
    session = await db.scalar(
        select(PrivateSession).where(PrivateSession.id == session_id).with_for_update()
    )
    if session is None:
        raise PermissionError("Private session is unavailable")
    if actor and actor.id not in {
        session.payer_user_id,
        (await db.get(CreatorProfile, session.creator_id)).user_id,
    }:
        raise PermissionError("Only the creator or payer can end this private session")
    if session.status in (PrivateSessionStatus.settled, PrivateSessionStatus.cancelled):
        return session
    if session.status is PrivateSessionStatus.active and session.active_started_at:
        session.billable_seconds += max(0, int((now - session.active_started_at).total_seconds()))
    # No required participants reached ACTIVE, so no service was delivered and
    # the configured minimum must not be charged.
    if session.billable_seconds == 0 and session.active_started_at is None:
        session.status, session.ended_at, session.end_reason = (
            PrivateSessionStatus.cancelled,
            now,
            reason,
        )
        session.ended_by_user_id = actor.id if actor else None
        return session
    session.status, session.ended_at, session.end_reason = PrivateSessionStatus.ended, now, reason
    session.ended_by_user_id = actor.id if actor else None
    return await settle_private_session(db, session)


async def expire_reconnect_grace(db: AsyncSession, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(seconds=get_settings().streaming_reconnect_grace_seconds)
    sessions = (
        await db.scalars(
            select(PrivateSession)
            .where(
                PrivateSession.status == PrivateSessionStatus.reconnecting,
                PrivateSession.disconnected_at <= cutoff,
            )
            .with_for_update()
        )
    ).all()
    for session in sessions:
        await end_private_session(db, None, session.id, "reconnect_grace_expired", now)
    return len(sessions)


async def reconcile_private_authorizations(db: AsyncSession, limit: int = 100) -> int:
    """Recover verified payment state after a webhook transaction interruption."""
    sessions = (
        await db.scalars(
            select(PrivateSession)
            .join(PaymentAttempt, PaymentAttempt.id == PrivateSession.payment_attempt_id)
            .where(
                PrivateSession.status == PrivateSessionStatus.awaiting_payment_authorization,
                PaymentAttempt.status == PaymentStatus.succeeded,
            )
            .order_by(PaymentAttempt.completed_at, PaymentAttempt.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for session in sessions:
        await authorize_private_session(db, session)
    return len(sessions)


async def process_private_provider_event(
    db: AsyncSession,
    *,
    event_id: str,
    event_type: str,
    session_id: UUID,
    user_id: UUID,
    now: datetime | None = None,
) -> PrivateSession | None:
    """Replay-safe provider adapter entrypoint; event IDs guard state/timing inflation."""
    if await db.scalar(
        select(ProviderLiveEvent).where(
            ProviderLiveEvent.provider == "livekit", ProviderLiveEvent.external_event_id == event_id
        )
    ):
        return None
    event = ProviderLiveEvent(
        provider="livekit",
        external_event_id=event_id,
        event_type=event_type,
        private_session_id=session_id,
        processed_at=now or datetime.now(UTC),
    )
    db.add(event)
    actor = await db.get(User, user_id)
    if actor is None:
        raise PermissionError("Provider participant is unknown")
    if event_type == "participant_joined":
        return await private_participant_connected(db, actor, session_id, now)
    if event_type == "participant_left":
        return await private_participant_disconnected(db, actor, session_id, now)
    raise StreamingError("Unsupported provider event")


def settlement_amount(session: PrivateSession) -> int:
    elapsed_charge = (session.per_minute_price_minor * session.billable_seconds + 59) // 60
    return min(session.max_authorization_minor, max(session.minimum_charge_minor, elapsed_charge))


async def settle_private_session(db: AsyncSession, session: PrivateSession) -> PrivateSession:
    if session.status is PrivateSessionStatus.settled:
        return session
    if session.status not in (PrivateSessionStatus.ended, PrivateSessionStatus.ending):
        raise StreamingError("Only ended private sessions can settle")
    gross = settlement_amount(session)
    fee, creator_amount = finance.commission_amount(gross, session.commission_basis_points)
    clearing = await finance._account(db, LedgerAccountKind.platform_clearing, session.currency)
    revenue = await finance._account(db, LedgerAccountKind.platform_revenue, session.currency)
    earnings = await finance._account(
        db, LedgerAccountKind.creator_pending, session.currency, session.creator_id
    )
    ledger = await finance.post_entries(
        db,
        transaction_type=LedgerTransactionType.private_live_session,
        currency=session.currency,
        idempotency_key=f"private_session:{session.id}",
        reference=f"private_session:{session.id}",
        entries=[
            (clearing, LedgerDirection.debit, gross),
            (revenue, LedgerDirection.credit, fee),
            (earnings, LedgerDirection.credit, creator_amount),
        ],
        metadata={
            "private_session_id": str(session.id),
            "billable_seconds": str(session.billable_seconds),
        },
    )
    from app.models.streaming import PrivateSessionSettlement

    settlement = await db.scalar(
        select(PrivateSessionSettlement).where(
            PrivateSessionSettlement.private_session_id == session.id
        )
    )
    if settlement is None:
        db.add(
            PrivateSessionSettlement(
                private_session_id=session.id,
                gross_amount_minor=gross,
                platform_fee_minor=fee,
                creator_amount_minor=creator_amount,
                currency=session.currency,
                billable_seconds=session.billable_seconds,
                ledger_transaction_id=ledger.id,
            )
        )
    session.status = PrivateSessionStatus.settled
    return session
