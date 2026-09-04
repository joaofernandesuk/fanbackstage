import logging
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db, OptionalIdentity
from app.compliance.http import resolve_request_compliance_decision
from app.compliance.types import ComplianceAccessError, ComplianceDecision
from app.core.config import get_settings
from app.core.http import RequestBodyTooLarge, read_limited_body
from app.core.rate_limit import enforce_streaming_rate_limit
from app.creators.service import resolve_creator_compliance_eligibilities
from app.finance.service import currency_code
from app.integrations.streaming import LiveKitStreamingProvider, StreamingProviderError
from app.models.compliance import ComplianceFeature
from app.models.creator import CreatorProfile, CreatorStatus
from app.models.identity import User
from app.models.streaming import (
    LiveAccessMode,
    LiveReport,
    LiveRoom,
    LiveVipShow,
    PrivateSessionMode,
)
from app.permissions.policies import Permission, authorize
from app.schemas.streaming import (
    ChatInput,
    CreatorLiveSettingsInput,
    CreatorLiveSettingsResponse,
    LiveAudienceResponse,
    LiveBanInput,
    LiveCommerceResponse,
    LiveCreatorSessionSummaryResponse,
    LiveEventResponse,
    LiveGiftCatalogItemResponse,
    LiveGiftInput,
    LiveGoalInput,
    LiveGoalResponse,
    LiveGoalUpdate,
    LivePrivatePeekOfferResponse,
    LivePrivatePeekPolicyInput,
    LivePrivatePeekPolicyResponse,
    LiveReactionInput,
    LiveReactionSummaryResponse,
    LiveReportInput,
    LiveRoomResponse,
    LiveSnapshotOfferResponse,
    LiveStartInput,
    LiveSupporterRankingEntry,
    LiveTipCatalogItemResponse,
    LiveTipInput,
    LiveVipShowInput,
    LiveVipShowResponse,
    PaidRequestInput,
    PaidRequestOptionInput,
    PaidRequestOptionResponse,
    PrivateInviteCandidateResponse,
    PrivateRequestInput,
    PrivateRequestResponse,
    PrivateSessionResponse,
    ProviderTokenResponse,
)
from app.streaming import service
from app.trust_safety import service as trust_safety_service

router = APIRouter(prefix="/live", tags=["streaming"])
logger = logging.getLogger("fanbackstage.streaming")
LIVEKIT_WEBHOOK_MAX_BYTES = 64 * 1024


async def private_request_response(db: Db, item) -> PrivateRequestResponse:
    invited = await db.get(User, item.invited_user_id) if item.invited_user_id else None
    creator_settings = await service.settings_for_creator(db, item.creator_id)
    peek_policy = await service.private_peek_policy(db)
    return PrivateRequestResponse(
        id=item.id,
        creator_id=item.creator_id,
        status=item.status.value,
        mode=item.mode.value,
        per_minute_price_minor=item.per_minute_price_minor,
        minimum_charge_minor=item.minimum_charge_minor,
        currency=item.currency,
        expires_at=item.expires_at,
        invitation_status=item.invitation_status.value,
        invited_viewer_label=service.private_invitee_label(invited),
        peeks_may_be_available=bool(creator_settings.private_peeks_enabled and peek_policy.active),
    )


def compliance_detail(exc: ComplianceAccessError) -> dict[str, object]:
    return {
        "message": exc.decision.reason,
        "code": exc.code,
        "action": exc.action,
    }


async def request_live_decision(db: Db, request: Request, user) -> ComplianceDecision:
    return await resolve_request_compliance_decision(
        db,
        request,
        user=user,
        feature=ComplianceFeature.live,
        adult_restricted=True,
    )


def room_response(
    room: LiveRoom, compliance_decision: ComplianceDecision | None = None
) -> LiveRoomResponse:
    # ``None`` is reserved for authenticated owner/moderation command
    # responses. Every public projection passes an explicit decision.
    allowed = compliance_decision is None or compliance_decision.allowed
    return LiveRoomResponse(
        id=room.id,
        public_id=room.public_id,
        creator_id=room.creator_id,
        status=room.status.value,
        access_mode=room.access_mode.value,
        title=room.title if allowed or compliance_decision is None else "Age-restricted live",
        description=room.description if allowed or compliance_decision is None else None,
        viewer_count=room.viewer_count,
        peak_viewer_count=room.peak_viewer_count,
        started_at=room.started_at,
        ended_at=room.ended_at,
        private_paused=room.active_private_session_id is not None,
        adult_access_required=True,
        adult_access_granted=bool(
            compliance_decision is None or compliance_decision.age_access_allowed
        ),
        compliance_allowed=allowed,
        compliance_code=compliance_decision.code if compliance_decision else "ALLOWED",
        compliance_action=(
            compliance_decision.action if compliance_decision and not allowed else None
        ),
        compliance_reason=compliance_decision.reason if compliance_decision else None,
    )


async def private_session_response(db, session, actor: User) -> PrivateSessionResponse:
    creator_user_id = await db.scalar(
        select(CreatorProfile.user_id).where(CreatorProfile.id == session.creator_id)
    )
    role = (
        "creator"
        if creator_user_id == actor.id
        else "payer"
        if session.payer_user_id == actor.id
        else "participant"
    )
    return PrivateSessionResponse(
        id=session.id,
        request_id=session.request_id,
        status=session.status.value,
        mode=session.mode.value,
        per_minute_price_minor=session.per_minute_price_minor,
        minimum_charge_minor=session.minimum_charge_minor,
        max_authorization_minor=session.max_authorization_minor,
        currency=session.currency,
        billable_seconds=session.billable_seconds,
        payment_attempt_id=session.payment_attempt_id if role == "payer" else None,
        participant_role=role,
        public_live_room_id=session.public_live_room_id,
        peeks_allowed=session.peeks_allowed,
        peek_price_minor=session.peek_price_minor,
        peek_currency=session.peek_currency,
        peek_commission_basis_points=(
            session.peek_commission_basis_points if role == "creator" else None
        ),
    )


def live_settings_response(settings) -> CreatorLiveSettingsResponse:
    return CreatorLiveSettingsResponse(
        private_sessions_enabled=settings.private_sessions_enabled,
        one_to_one_price_minor=settings.one_to_one_price_minor,
        two_to_one_price_minor=settings.two_to_one_price_minor,
        currency=settings.currency,
        minimum_minutes=settings.minimum_minutes,
        max_authorization_minor=settings.max_authorization_minor,
        snapshots_enabled=settings.snapshots_enabled,
        snapshot_price_minor=settings.snapshot_price_minor,
        private_peeks_enabled=settings.private_peeks_enabled,
    )


def live_commerce_response(charge) -> LiveCommerceResponse:
    return LiveCommerceResponse(
        id=charge.id,
        status=charge.status.value,
        kind=charge.kind.value,
        gross_amount_minor=charge.gross_amount_minor,
        currency=charge.currency,
        payment_attempt_id=charge.payment_attempt_id,
        request_label=charge.request_label,
        request_message=charge.request_message,
        expires_at=charge.expires_at,
        resolved_at=charge.resolved_at,
    )


def live_vip_show_response(
    show: LiveVipShow, *, confirmed_amount_minor: int, viewer_admitted: bool
) -> LiveVipShowResponse:
    return LiveVipShowResponse(
        id=show.id,
        live_room_id=show.live_room_id,
        status=show.status.value,
        title=show.title,
        description=show.description,
        goal_amount_minor=show.goal_amount_minor,
        confirmed_amount_minor=confirmed_amount_minor,
        buy_in_amount_minor=show.buy_in_amount_minor,
        currency=show.currency,
        preshow_ends_at=show.preshow_ends_at,
        duration_seconds=show.duration_seconds,
        started_at=show.started_at,
        ends_at=show.ends_at,
        viewer_admitted=viewer_admitted,
    )


@router.post("/webhooks/livekit", status_code=204)
async def livekit_webhook(request: Request, db: Db) -> None:
    """Accept only signed raw LiveKit events; the browser never reports presence."""
    try:
        body = await read_limited_body(request, max_bytes=LIVEKIT_WEBHOOK_MAX_BYTES)
    except RequestBodyTooLarge as exc:
        raise HTTPException(413, "Live provider webhook is too large") from exc
    except ValueError as exc:
        raise HTTPException(400, "Invalid live provider request framing") from exc
    try:
        event = LiveKitStreamingProvider().verify_webhook(
            body, request.headers.get("Authorization")
        )
        await service.process_livekit_webhook(db, event)
        await db.commit()
    except StreamingProviderError as exc:
        await db.rollback()
        # Do not acknowledge a signed join event until the corresponding
        # provider eviction succeeds; LiveKit can then retry the same event.
        logger.error("livekit_webhook_control_retry_required")
        raise HTTPException(
            503,
            "Live provider control is temporarily unavailable",
        ) from exc
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
        decision = await request_live_decision(db, request, identity[0])
        room = await service.start_live(
            db,
            identity[0],
            payload.title,
            LiveAccessMode(payload.access_mode),
            payload.description,
            compliance_decision=decision,
        )
        await db.commit()
        return room_response(room, decision)
    except ComplianceAccessError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.get("/rooms/mine", response_model=LiveRoomResponse | None)
async def current_creator_room(identity: CurrentIdentity, db: Db) -> LiveRoomResponse | None:
    """Let a creator recover their existing room after a Studio reload."""

    room = await service.current_creator_public_live_room(db, identity[0])
    return room_response(room) if room else None


@router.post("/rooms/{room_id}/end", response_model=LiveRoomResponse)
async def end_room(room_id: UUID, identity: CurrentIdentity, db: Db) -> LiveRoomResponse:
    try:
        room = await service.end_live(db, identity[0], room_id)
        await db.commit()
        return room_response(room)
    except StreamingProviderError as exc:
        await db.rollback()
        raise HTTPException(503, "Live provider control is temporarily unavailable") from exc
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.get("/rooms", response_model=list[LiveRoomResponse])
async def discovery(request: Request, identity: OptionalIdentity, db: Db) -> list[LiveRoomResponse]:
    user = identity[0] if identity else None
    decision = await request_live_decision(db, request, user)
    rooms = (
        await db.scalars(
            select(LiveRoom)
            .join(CreatorProfile, CreatorProfile.id == LiveRoom.creator_id)
            .where(
                LiveRoom.status == "live",
                CreatorProfile.status == CreatorStatus.approved,
                CreatorProfile.is_public.is_(True),
            )
            .order_by(LiveRoom.started_at.desc())
        )
    ).all()
    profiles = list(
        await db.scalars(
            select(CreatorProfile).where(CreatorProfile.id.in_({room.creator_id for room in rooms}))
        )
    )
    eligibility = await resolve_creator_compliance_eligibilities(db, profiles=profiles)
    return [
        room_response(room, decision)
        for room in rooms
        if eligibility.get(room.creator_id) and eligibility[room.creator_id].public_allowed
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
        if settings.snapshot_price_minor > settings.max_authorization_minor:
            raise ValueError("Snapshot price must not exceed the authorization cap")
        await db.commit()
        return live_settings_response(settings)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.get("/admin/private-peek-policy", response_model=LivePrivatePeekPolicyResponse)
async def get_private_peek_policy(
    identity: CurrentIdentity, db: Db
) -> LivePrivatePeekPolicyResponse:
    authorize(identity[0], Permission.FINANCIAL_ACCESS)
    policy = await service.private_peek_policy(db)
    return LivePrivatePeekPolicyResponse.model_validate(policy, from_attributes=True)


@router.get("/private-peek-policy", response_model=LivePrivatePeekPolicyResponse)
async def get_creator_private_peek_policy(
    identity: CurrentIdentity, db: Db
) -> LivePrivatePeekPolicyResponse:
    await service.approved_creator(db, identity[0])
    policy = await service.private_peek_policy(db)
    return LivePrivatePeekPolicyResponse.model_validate(policy, from_attributes=True)


@router.put("/admin/private-peek-policy", response_model=LivePrivatePeekPolicyResponse)
async def set_private_peek_policy(
    payload: LivePrivatePeekPolicyInput, identity: CurrentIdentity, db: Db
) -> LivePrivatePeekPolicyResponse:
    try:
        authorize(identity[0], Permission.FINANCIAL_CONFIGURE)
        if not payload.confirmed:
            raise ValueError("Confirmation is required")
        policy = await service.update_private_peek_policy(
            db,
            identity[0],
            active=payload.active,
            amount_minor=payload.amount_minor,
            currency=payload.currency,
            commission_basis_points=payload.commission_basis_points,
            reason=payload.reason,
        )
        await db.commit()
        return LivePrivatePeekPolicyResponse.model_validate(policy, from_attributes=True)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/rooms/{room_id}/join")
async def join_room(room_id: UUID, request: Request, identity: CurrentIdentity, db: Db) -> dict:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "live_join")
        decision = await request_live_decision(db, request, identity[0])
        participant = await service.join_live(
            db, identity[0], room_id, compliance_decision=decision
        )
        await db.commit()
        return {"room_id": str(participant.live_room_id), "role": participant.role.value}
    except ComplianceAccessError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(403, str(exc)) from exc


@router.post("/rooms/{room_id}/token", response_model=ProviderTokenResponse)
async def room_token(room_id: UUID, request: Request, identity: CurrentIdentity, db: Db) -> dict:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "live_token")
        decision = await request_live_decision(db, request, identity[0])
        room, token = await service.issue_live_token(
            db, identity[0], room_id, compliance_decision=decision
        )
        await db.commit()
        return {
            "room_id": str(room.id),
            "provider_url": get_settings().livekit_url,
            "token": token,
        }
    except ComplianceAccessError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(403, str(exc)) from exc


@router.post("/private-sessions/{session_id}/token", response_model=ProviderTokenResponse)
async def private_session_token(
    session_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> ProviderTokenResponse:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "private_live_token")
        decision = await request_live_decision(db, request, identity[0])
        session, token = await service.issue_private_token(
            db, identity[0], session_id, compliance_decision=decision
        )
        await db.commit()
        return ProviderTokenResponse(
            room_id=session.id,
            provider_url=get_settings().livekit_url,
            token=token,
        )
    except ComplianceAccessError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(403, str(exc)) from exc


@router.get("/rooms/{room_id}/private-peek", response_model=LivePrivatePeekOfferResponse)
async def private_peek_offer(
    room_id: UUID, identity: CurrentIdentity, db: Db
) -> LivePrivatePeekOfferResponse:
    try:
        return LivePrivatePeekOfferResponse(
            **await service.private_peek_offer(db, identity[0], room_id)
        )
    except (PermissionError, ValueError) as exc:
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/rooms/{room_id}/private-peek", response_model=LiveCommerceResponse)
async def purchase_private_peek(
    room_id: UUID,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> LiveCommerceResponse:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "private_live_peek")
        charge = await service.initiate_private_peek(
            db,
            identity[0],
            room_id,
            idempotency_key or "",
            compliance_decision=await request_live_decision(db, request, identity[0]),
        )
        await db.commit()
        return live_commerce_response(charge)
    except ComplianceAccessError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/rooms/{room_id}/private-peek/token", response_model=ProviderTokenResponse)
async def private_peek_token(
    room_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> ProviderTokenResponse:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "private_live_peek_token")
        session, token = await service.issue_private_peek_token(
            db,
            identity[0],
            room_id,
            compliance_decision=await request_live_decision(db, request, identity[0]),
        )
        await db.commit()
        return ProviderTokenResponse(
            room_id=session.id,
            provider_url=get_settings().livekit_url,
            token=token,
        )
    except ComplianceAccessError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/rooms/{room_id}/vip-show", response_model=LiveVipShowResponse)
async def create_vip_show(
    room_id: UUID,
    payload: LiveVipShowInput,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
) -> LiveVipShowResponse:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "live_vip_create")
        show = await service.create_live_vip_show(
            db,
            identity[0],
            room_id,
            title=payload.title,
            description=payload.description,
            goal_amount_minor=payload.goal_amount_minor,
            buy_in_amount_minor=payload.buy_in_amount_minor,
            preshow_minutes=payload.preshow_minutes,
            duration_minutes=payload.duration_minutes,
        )
        await db.commit()
        return live_vip_show_response(show, confirmed_amount_minor=0, viewer_admitted=False)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.get("/rooms/{room_id}/vip-show", response_model=LiveVipShowResponse | None)
async def vip_show_status(
    room_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> LiveVipShowResponse | None:
    try:
        decision = await request_live_decision(db, request, identity[0])
        show, confirmed, admitted = await service.live_vip_show_for_room(
            db, identity[0], room_id, compliance_decision=decision
        )
        await db.commit()
        return (
            live_vip_show_response(show, confirmed_amount_minor=confirmed, viewer_admitted=admitted)
            if show
            else None
        )
    except ComplianceAccessError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(403, str(exc)) from exc


@router.post("/rooms/{room_id}/vip-show/admission", response_model=LiveCommerceResponse)
async def buy_vip_admission(
    room_id: UUID,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> LiveCommerceResponse:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "live_vip_admission")
        decision = await request_live_decision(db, request, identity[0])
        charge = await service.initiate_live_vip_admission(
            db,
            identity[0],
            room_id,
            idempotency_key,
            compliance_decision=decision,
        )
        await db.commit()
        return live_commerce_response(charge)
    except ComplianceAccessError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/rooms/{room_id}/vip-show/start", response_model=LiveVipShowResponse)
async def start_vip_show(
    room_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> LiveVipShowResponse:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "live_vip_start")
        show = await db.scalar(service.current_vip_show_statement(room_id))
        if show is None:
            raise PermissionError("VIP show not found")
        show = await service.activate_live_vip_show(db, identity[0], show.id)
        confirmed = await service._vip_confirmed_total(db, show.id)
        await db.commit()
        return live_vip_show_response(show, confirmed_amount_minor=confirmed, viewer_admitted=False)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/rooms/{room_id}/vip-show/cancel", response_model=LiveVipShowResponse)
async def cancel_vip_show(
    room_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> LiveVipShowResponse:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "live_vip_cancel")
        show = await db.scalar(service.current_vip_show_statement(room_id))
        if show is None:
            raise PermissionError("VIP show not found")
        show = await service.cancel_live_vip_show(db, identity[0], show.id)
        confirmed = await service._vip_confirmed_total(db, show.id)
        await db.commit()
        return live_vip_show_response(show, confirmed_amount_minor=confirmed, viewer_admitted=False)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/rooms/{room_id}/chat")
async def chat(
    room_id: UUID, payload: ChatInput, request: Request, identity: CurrentIdentity, db: Db
) -> dict:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "live_chat")
        decision = await request_live_decision(db, request, identity[0])
        message = await service.post_chat(
            db, identity[0], room_id, payload.body, compliance_decision=decision
        )
        labels = await service.live_actor_labels(db, {identity[0].id})
        await db.commit()
        return {
            "id": str(message.id),
            "body": message.body,
            "sender_user_id": str(identity[0].id),
            "sender_label": labels[identity[0].id],
        }
    except ComplianceAccessError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(403, str(exc)) from exc


@router.get("/rooms/{room_id}/chat")
async def chat_history(
    room_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> list[dict]:
    try:
        decision = await request_live_decision(db, request, identity[0])
        items = await service.live_chat_history(
            db, identity[0], room_id, compliance_decision=decision
        )
        labels = await service.live_actor_labels(
            db, {item.sender_user_id for item in items if item.sender_user_id}
        )
        return [
            {
                "id": str(item.id),
                "body": item.body,
                "sender_user_id": str(item.sender_user_id) if item.sender_user_id else None,
                "sender_label": labels.get(item.sender_user_id, "System")
                if item.sender_user_id
                else "System",
            }
            for item in items
        ]
    except ComplianceAccessError as exc:
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/rooms/{room_id}/audience", response_model=LiveAudienceResponse)
async def live_room_audience(
    room_id: UUID, identity: CurrentIdentity, db: Db
) -> LiveAudienceResponse:
    try:
        return LiveAudienceResponse(
            **(await service.creator_live_audience(db, identity[0], room_id))
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/rooms/{room_id}/creator-summary", response_model=LiveCreatorSessionSummaryResponse)
async def live_room_creator_summary(
    room_id: UUID, identity: CurrentIdentity, db: Db
) -> LiveCreatorSessionSummaryResponse:
    try:
        return LiveCreatorSessionSummaryResponse(
            **(await service.creator_live_session_summary(db, identity[0], room_id))
        )
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/rooms/{room_id}/activity", response_model=list[LiveEventResponse])
async def activity_history(
    room_id: UUID,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
    limit: int = Query(default=100, ge=1, le=100),
) -> list[LiveEventResponse]:
    try:
        decision = await request_live_decision(db, request, identity[0])
        events = await service.live_activity_history(
            db, identity[0], room_id, compliance_decision=decision, limit=limit
        )
        return [
            LiveEventResponse(
                id=event.id,
                event_type=event.event_type,
                actor_user_id=event.actor_user_id,
                amount_minor=event.amount_minor,
                currency=event.currency,
                source_type=event.source_type,
                source_id=event.source_id,
                metadata=event.metadata_json,
                occurred_at=event.occurred_at,
                created_at=event.created_at,
            )
            for event in events
        ]
    except ComplianceAccessError as exc:
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/rooms/{room_id}/tips", response_model=LiveCommerceResponse)
async def tip_live_room(
    room_id: UUID,
    payload: LiveTipInput,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> LiveCommerceResponse:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "live_tip")
        charge = await service.initiate_live_tip(
            db,
            identity[0],
            room_id,
            idempotency_key or "",
            tip_catalog_item_id=payload.tip_catalog_item_id,
            compliance_decision=await request_live_decision(db, request, identity[0]),
        )
        await db.commit()
        return live_commerce_response(charge)
    except ComplianceAccessError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.get("/rooms/{room_id}/snapshot-offer", response_model=LiveSnapshotOfferResponse)
async def snapshot_offer(
    room_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> LiveSnapshotOfferResponse:
    try:
        settings = await service.live_snapshot_offer(
            db,
            identity[0],
            room_id,
            compliance_decision=await request_live_decision(db, request, identity[0]),
        )
        return LiveSnapshotOfferResponse(
            enabled=settings.snapshots_enabled,
            amount_minor=settings.snapshot_price_minor,
            currency=settings.currency,
        )
    except ComplianceAccessError as exc:
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/rooms/{room_id}/snapshots", response_model=LiveCommerceResponse)
async def purchase_snapshot(
    room_id: UUID,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> LiveCommerceResponse:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "live_snapshot")
        charge = await service.initiate_live_snapshot(
            db,
            identity[0],
            room_id,
            idempotency_key or "",
            compliance_decision=await request_live_decision(db, request, identity[0]),
        )
        await db.commit()
        return live_commerce_response(charge)
    except ComplianceAccessError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.get("/rooms/{room_id}/tip-menu", response_model=list[LiveTipCatalogItemResponse])
async def tip_menu_for_live_room(
    room_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> list[LiveTipCatalogItemResponse]:
    try:
        items = await service.room_tip_menu(
            db,
            identity[0],
            room_id,
            compliance_decision=await request_live_decision(db, request, identity[0]),
        )
        return [
            LiveTipCatalogItemResponse.model_validate(item, from_attributes=True) for item in items
        ]
    except ComplianceAccessError as exc:
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/rooms/{room_id}/gifts", response_model=list[LiveGiftCatalogItemResponse])
async def gifts_for_live_room(
    room_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> list[LiveGiftCatalogItemResponse]:
    try:
        items = await service.room_gift_catalog(
            db,
            identity[0],
            room_id,
            compliance_decision=await request_live_decision(db, request, identity[0]),
        )
        return [
            LiveGiftCatalogItemResponse.model_validate(item, from_attributes=True) for item in items
        ]
    except ComplianceAccessError as exc:
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/rooms/{room_id}/gifts", response_model=LiveCommerceResponse)
async def gift_live_room(
    room_id: UUID,
    payload: LiveGiftInput,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> LiveCommerceResponse:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "live_gift")
        charge = await service.initiate_live_gift(
            db,
            identity[0],
            room_id,
            payload.gift_catalog_item_id,
            idempotency_key or "",
            compliance_decision=await request_live_decision(db, request, identity[0]),
        )
        await db.commit()
        return live_commerce_response(charge)
    except ComplianceAccessError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.get(
    "/rooms/{room_id}/paid-request-options",
    response_model=list[PaidRequestOptionResponse],
)
async def paid_request_options_for_room(
    room_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> list[PaidRequestOptionResponse]:
    try:
        items = await service.room_paid_request_options(
            db,
            identity[0],
            room_id,
            compliance_decision=await request_live_decision(db, request, identity[0]),
        )
        return [
            PaidRequestOptionResponse.model_validate(item, from_attributes=True) for item in items
        ]
    except ComplianceAccessError as exc:
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/rooms/{room_id}/paid-requests", response_model=LiveCommerceResponse)
async def submit_paid_request(
    room_id: UUID,
    payload: PaidRequestInput,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> LiveCommerceResponse:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "live_paid_request")
        charge = await service.initiate_live_paid_request(
            db,
            identity[0],
            room_id,
            payload.option_id,
            payload.message,
            idempotency_key or "",
            compliance_decision=await request_live_decision(db, request, identity[0]),
        )
        await db.commit()
        return live_commerce_response(charge)
    except ComplianceAccessError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.get("/paid-request-options", response_model=list[PaidRequestOptionResponse])
async def my_paid_request_options(
    identity: CurrentIdentity, db: Db
) -> list[PaidRequestOptionResponse]:
    return [
        PaidRequestOptionResponse.model_validate(item, from_attributes=True)
        for item in await service.creator_paid_request_options(db, identity[0])
    ]


@router.post("/paid-request-options", response_model=PaidRequestOptionResponse)
async def create_paid_request_option(
    payload: PaidRequestOptionInput, identity: CurrentIdentity, db: Db
) -> PaidRequestOptionResponse:
    try:
        item = await service.save_paid_request_option(db, identity[0], **payload.model_dump())
        await db.commit()
        return PaidRequestOptionResponse.model_validate(item, from_attributes=True)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.put("/paid-request-options/{option_id}", response_model=PaidRequestOptionResponse)
async def update_paid_request_option(
    option_id: UUID,
    payload: PaidRequestOptionInput,
    identity: CurrentIdentity,
    db: Db,
) -> PaidRequestOptionResponse:
    try:
        item = await service.save_paid_request_option(
            db, identity[0], option_id=option_id, **payload.model_dump()
        )
        await db.commit()
        return PaidRequestOptionResponse.model_validate(item, from_attributes=True)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.get("/paid-requests/mine/creator", response_model=list[LiveCommerceResponse])
async def my_pending_paid_requests(identity: CurrentIdentity, db: Db) -> list[LiveCommerceResponse]:
    return [
        live_commerce_response(item)
        for item in await service.creator_pending_paid_requests(db, identity[0])
    ]


@router.post("/paid-requests/{charge_id}/accept", response_model=LiveCommerceResponse)
async def accept_paid_request(
    charge_id: UUID, identity: CurrentIdentity, db: Db
) -> LiveCommerceResponse:
    try:
        charge = await service.accept_paid_request(db, identity[0], charge_id)
        await db.commit()
        return live_commerce_response(charge)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/paid-requests/{charge_id}/decline", response_model=LiveCommerceResponse)
async def decline_paid_request(
    charge_id: UUID, identity: CurrentIdentity, db: Db
) -> LiveCommerceResponse:
    try:
        charge = await service.decline_paid_request(db, identity[0], charge_id)
        await db.commit()
        return live_commerce_response(charge)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/rooms/{room_id}/reactions", response_model=LiveReactionSummaryResponse)
async def react_to_live_room(
    room_id: UUID,
    payload: LiveReactionInput,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
) -> LiveReactionSummaryResponse:
    try:
        await enforce_streaming_rate_limit(request, str(identity[0].id), "live_reaction")
        counts = await service.add_live_reaction(
            db,
            identity[0],
            room_id,
            payload.reaction_type,
            compliance_decision=await request_live_decision(db, request, identity[0]),
        )
        await db.commit()
        return LiveReactionSummaryResponse(counts=counts)
    except ComplianceAccessError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.get("/rooms/{room_id}/reactions", response_model=LiveReactionSummaryResponse)
async def live_room_reactions(
    room_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> LiveReactionSummaryResponse:
    try:
        counts = await service.live_reaction_summary(
            db,
            identity[0],
            room_id,
            compliance_decision=await request_live_decision(db, request, identity[0]),
        )
        return LiveReactionSummaryResponse(counts=counts)
    except ComplianceAccessError as exc:
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/rooms/{room_id}/supporters", response_model=list[LiveSupporterRankingEntry])
async def live_room_supporters(
    room_id: UUID,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
    limit: int = Query(default=10, ge=1, le=25),
) -> list[LiveSupporterRankingEntry]:
    try:
        return [
            LiveSupporterRankingEntry(**row)
            for row in await service.live_supporter_ranking(
                db,
                identity[0],
                room_id,
                compliance_decision=await request_live_decision(db, request, identity[0]),
                limit=limit,
            )
        ]
    except ComplianceAccessError as exc:
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/tip-menu", response_model=list[LiveTipCatalogItemResponse])
async def shared_tip_catalog(identity: CurrentIdentity, db: Db) -> list[LiveTipCatalogItemResponse]:
    items = await service.platform_tip_catalog(db, identity[0])
    return [LiveTipCatalogItemResponse.model_validate(item, from_attributes=True) for item in items]


@router.get("/gifts", response_model=list[LiveGiftCatalogItemResponse])
async def shared_gift_catalog(
    identity: CurrentIdentity, db: Db
) -> list[LiveGiftCatalogItemResponse]:
    items = await service.platform_gift_catalog(db, identity[0])
    return [
        LiveGiftCatalogItemResponse.model_validate(item, from_attributes=True) for item in items
    ]


@router.post("/goals", response_model=LiveGoalResponse)
async def create_goal(
    payload: LiveGoalInput, identity: CurrentIdentity, db: Db
) -> LiveGoalResponse:
    try:
        goal = await service.create_live_goal(db, identity[0], **payload.model_dump())
        await db.commit()
        return LiveGoalResponse.model_validate(goal, from_attributes=True)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.get("/goals", response_model=list[LiveGoalResponse])
async def my_goals(identity: CurrentIdentity, db: Db) -> list[LiveGoalResponse]:
    try:
        return [
            LiveGoalResponse.model_validate(goal, from_attributes=True)
            for goal in await service.creator_live_goals(db, identity[0])
        ]
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.put("/goals/{goal_id}", response_model=LiveGoalResponse)
async def update_goal(
    goal_id: UUID, payload: LiveGoalUpdate, identity: CurrentIdentity, db: Db
) -> LiveGoalResponse:
    try:
        goal = await service.update_live_goal(db, identity[0], goal_id, **payload.model_dump())
        await db.commit()
        return LiveGoalResponse.model_validate(goal, from_attributes=True)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/goals/{goal_id}/reset", response_model=LiveGoalResponse)
async def reset_goal(goal_id: UUID, identity: CurrentIdentity, db: Db) -> LiveGoalResponse:
    try:
        goal = await service.reset_live_goal(db, identity[0], goal_id)
        await db.commit()
        return LiveGoalResponse.model_validate(goal, from_attributes=True)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.get("/rooms/{room_id}/goals", response_model=list[LiveGoalResponse])
async def room_goals(room_id: UUID, identity: CurrentIdentity, db: Db) -> list[LiveGoalResponse]:
    try:
        return [
            LiveGoalResponse.model_validate(goal, from_attributes=True).model_copy(
                update={"progress_amount_minor": progress}
            )
            for goal, progress in await service.live_goal_progress(db, identity[0], room_id)
        ]
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


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
    except StreamingProviderError as exc:
        await db.rollback()
        raise HTTPException(503, "Live provider control is temporarily unavailable") from exc
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(403, str(exc)) from exc


@router.post("/rooms/{room_id}/reports")
async def report_room(
    room_id: UUID, payload: LiveReportInput, identity: CurrentIdentity, db: Db
) -> dict:
    try:
        target_type = (
            trust_safety_service.ReportTargetType.live_chat_message
            if payload.chat_message_id
            else trust_safety_service.ReportTargetType.live_room
        )
        report, case, duplicate = await trust_safety_service.open_or_attach_report(
            db,
            identity[0],
            target_type=target_type,
            target_id=payload.chat_message_id or room_id,
            reason=trust_safety_service.ReportReason(payload.reason),
            details=payload.details,
        )
        await db.commit()
        return {
            "id": str(report.id),
            "case_id": case.public_id,
            "duplicate": duplicate,
        }
    except (PermissionError, ValueError, trust_safety_service.TrustSafetyError) as exc:
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
        decision = await request_live_decision(db, request, identity[0])
        item = await service.request_private_session(
            db,
            identity[0],
            creator_id,
            PrivateSessionMode(payload.mode),
            payload.invited_user_id,
            payload.note,
            compliance_decision=decision,
        )
        await db.commit()
        return await private_request_response(db, item)
    except ComplianceAccessError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.get(
    "/creators/{creator_id}/private-invite-candidates",
    response_model=list[PrivateInviteCandidateResponse],
)
async def private_invite_candidates(
    creator_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> list[PrivateInviteCandidateResponse]:
    await enforce_streaming_rate_limit(request, str(identity[0].id), "private_invite_candidates")
    try:
        return [
            PrivateInviteCandidateResponse(user_id=user.id, label=label)
            for user, label in await service.eligible_private_invitees(db, identity[0], creator_id)
        ]
    except (PermissionError, ValueError) as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/private-invitations/mine", response_model=list[PrivateRequestResponse])
async def my_private_invitations(identity: CurrentIdentity, db: Db) -> list[PrivateRequestResponse]:
    return [
        await private_request_response(db, item)
        for item in await service.invited_private_requests(db, identity[0])
    ]


@router.post("/private-invitations/{request_id}/{decision}", response_model=PrivateRequestResponse)
async def decide_private_invitation(
    request_id: UUID, decision: str, identity: CurrentIdentity, db: Db
) -> PrivateRequestResponse:
    if decision not in {"accept", "decline"}:
        raise HTTPException(404, "Invitation action not found")
    try:
        item = await service.resolve_private_invitation(
            db, identity[0], request_id, accept=decision == "accept"
        )
        await db.commit()
        return await private_request_response(db, item)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.get("/private-requests/mine/creator", response_model=list[PrivateRequestResponse])
async def creator_private_requests(
    identity: CurrentIdentity, db: Db
) -> list[PrivateRequestResponse]:
    try:
        rows = await service.creator_pending_private_requests(db, identity[0])
        return [await private_request_response(db, item) for item in rows]
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.get("/private-sessions/mine", response_model=list[PrivateSessionResponse])
async def my_private_sessions(identity: CurrentIdentity, db: Db) -> list[PrivateSessionResponse]:
    return [
        await private_session_response(db, session, identity[0])
        for session in await service.participant_private_sessions(db, identity[0])
    ]


@router.post("/private-requests/{request_id}/accept", response_model=PrivateSessionResponse)
async def accept_private(
    request_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> PrivateSessionResponse:
    try:
        decision = await request_live_decision(db, request, identity[0])
        session = await service.accept_private_request(
            db, identity[0], request_id, compliance_decision=decision
        )
        await db.commit()
        return await private_session_response(db, session, identity[0])
    except ComplianceAccessError as exc:
        await db.rollback()
        raise HTTPException(exc.status_code, compliance_detail(exc)) from exc
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/private-requests/{request_id}/decline", response_model=PrivateRequestResponse)
async def decline_private(
    request_id: UUID, identity: CurrentIdentity, db: Db
) -> PrivateRequestResponse:
    try:
        item = await service.decline_private_request(db, identity[0], request_id)
        await db.commit()
        return await private_request_response(db, item)
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
        return await private_session_response(db, session, identity[0])
    except StreamingProviderError as exc:
        await db.rollback()
        raise HTTPException(503, "Live provider control is temporarily unavailable") from exc
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc
