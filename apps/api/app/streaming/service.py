"""Streaming domain state. LiveKit transports media; PostgreSQL owns product truth."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.compliance.http import compose_legal_acceptance_decision
from app.compliance.locks import lock_compliance_subject, lock_compliance_subjects
from app.compliance.policy import resolve_compliance_decision
from app.compliance.types import (
    ComplianceDecision,
    JurisdictionSignals,
    require_compliance_access,
)
from app.core.config import get_settings
from app.creators.service import (
    require_public_creator_access,
    resolve_creator_compliance_eligibility,
)
from app.finance import service as finance
from app.finance.providers import new_provider_reference
from app.finance.service import currency_code, ppv_commission
from app.integrations.streaming import LiveKitStreamingProvider, StreamingProviderError
from app.media.service import approved_creator
from app.models.compliance import ComplianceFeature
from app.models.content import ContentEntitlement, EntitlementStatus
from app.models.creator import CreatorProfile, CreatorStatus
from app.models.finance import (
    ExcessCaptureSource,
    LedgerAccountKind,
    LedgerDirection,
    LedgerEntry,
    LedgerTransaction,
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
    LiveCommerceCharge,
    LiveCommerceKind,
    LiveCommerceStatus,
    LiveEvent,
    LiveGiftCatalogItem,
    LiveGoal,
    LivePaidRequestOption,
    LiveParticipant,
    LiveParticipantRole,
    LiveProviderControlAction,
    LiveProviderControlIntent,
    LiveReactionAggregate,
    LiveReactionType,
    LiveRecording,
    LiveRecordingStatus,
    LiveReport,
    LiveRoom,
    LiveRoomStatus,
    LiveTipMenuItem,
    PrivateRequestStatus,
    PrivateSession,
    PrivateSessionMode,
    PrivateSessionRequest,
    PrivateSessionSettlement,
    PrivateSessionStatus,
    ProviderLiveEvent,
    SessionParticipant,
    SessionParticipantRole,
)
from app.notifications.service import emit_transactional
from app.streaming.control_outbox import (
    LiveProviderControlStructuralError,
    enqueue_live_provider_control_intent,
)


class StreamingError(ValueError):
    pass


async def record_live_event(
    db: AsyncSession,
    *,
    event_type: str,
    idempotency_key: str,
    live_room_id: UUID | None = None,
    private_session_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    ledger_transaction_id: UUID | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    amount_minor: int | None = None,
    currency: str | None = None,
    metadata: dict | None = None,
) -> LiveEvent:
    """Append exactly one recoverable activity event for an authoritative fact."""

    existing = await db.scalar(
        select(LiveEvent).where(LiveEvent.idempotency_key == idempotency_key).with_for_update()
    )
    if existing is not None:
        return existing
    if not live_room_id and not private_session_id:
        raise StreamingError("A Live event requires a room or private-session context")
    event = LiveEvent(
        live_room_id=live_room_id,
        private_session_id=private_session_id,
        event_type=event_type,
        occurred_at=datetime.now(UTC),
        actor_user_id=actor_user_id,
        ledger_transaction_id=ledger_transaction_id,
        source_type=source_type,
        source_id=source_id,
        amount_minor=amount_minor,
        currency=currency_code(currency) if currency else None,
        idempotency_key=idempotency_key,
        metadata_json=metadata or {},
    )
    db.add(event)
    await db.flush()
    return event


def livekit_control_provider() -> LiveKitStreamingProvider:
    """Return the server-side LiveKit control adapter.

    Keeping this boundary separate from token construction lets integration
    tests replace destructive room controls without weakening production
    behavior or fabricating browser authority.
    """

    return LiveKitStreamingProvider()


def _authority_expiry(*values: datetime | None) -> datetime | None:
    normalized = [
        value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        for value in values
        if value is not None
    ]
    return min(normalized) if normalized else None


async def _active_creator_entitlement(
    db: AsyncSession,
    *,
    user_id: UUID,
    creator_id: UUID,
    now: datetime | None = None,
) -> ContentEntitlement | None:
    current = now or datetime.now(UTC)
    return await db.scalar(
        select(ContentEntitlement)
        .where(
            ContentEntitlement.subject_user_id == user_id,
            ContentEntitlement.creator_id == creator_id,
            ContentEntitlement.status == EntitlementStatus.active,
            ContentEntitlement.valid_from <= current,
            or_(
                ContentEntitlement.valid_until.is_(None),
                ContentEntitlement.valid_until > current,
            ),
        )
        .order_by(ContentEntitlement.valid_until.desc().nulls_last())
    )


async def require_live_compliance(
    db: AsyncSession,
    user: User,
    decision: ComplianceDecision | None = None,
) -> ComplianceDecision:
    """Apply the same viewer-age authority before every live mutation or read."""
    # A route-supplied decision carries trusted request-country/legal context,
    # but it may have been computed before this transaction acquired the
    # subject authority lock. Preserve any denial, then re-resolve current
    # evidence under the lock rather than trusting a stale allowance.
    if decision is not None:
        require_compliance_access(decision)
    resolved = await resolve_compliance_decision(
        db,
        user=user,
        feature=ComplianceFeature.live,
        signals=(
            JurisdictionSignals(trusted_proxy_country=decision.jurisdiction)
            if decision is not None and decision.jurisdiction is not None
            else None
        ),
        adult_restricted=True,
    )
    resolved, _ = await compose_legal_acceptance_decision(
        db,
        user=user,
        decision=resolved,
    )
    return require_compliance_access(resolved)


async def require_private_purchase_compliance(db: AsyncSession, user: User) -> ComplianceDecision:
    return require_compliance_access(
        await resolve_compliance_decision(
            db,
            user=user,
            feature=ComplianceFeature.purchases,
            adult_restricted=True,
        )
    )


async def _private_session_authority_allowed(
    db: AsyncSession,
    session: PrivateSession,
) -> bool:
    """Re-resolve every authority required to keep a private room connected."""

    creator = await db.get(CreatorProfile, session.creator_id)
    if (
        creator is None
        or creator.status is not CreatorStatus.approved
        or not creator.is_public
        or not (await resolve_creator_compliance_eligibility(db, profile=creator)).public_allowed
    ):
        return False
    creator_user = await db.get(User, creator.user_id)
    if creator_user is None:
        return False
    try:
        await require_live_compliance(db, creator_user)
    except PermissionError:
        return False

    participants = (
        await db.scalars(
            select(SessionParticipant).where(SessionParticipant.private_session_id == session.id)
        )
    ).all()
    if not participants:
        return False
    participant_user_ids = {participant.user_id for participant in participants}
    participant_user_ids.add(creator.user_id)
    if await db.scalar(
        select(UserBlock.id).where(
            UserBlock.blocker_user_id.in_(participant_user_ids),
            UserBlock.blocked_user_id.in_(participant_user_ids),
        )
    ):
        return False
    for participant in participants:
        user = await db.get(User, participant.user_id)
        if user is None:
            return False
        try:
            await require_live_compliance(db, user)
        except PermissionError:
            return False
    return True


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


async def _live_commerce_room(
    db: AsyncSession, buyer: User, room_id: UUID, compliance_decision: ComplianceDecision | None
) -> tuple[LiveRoom, CreatorProfile, CreatorLiveSettings]:
    await require_live_compliance(db, buyer, compliance_decision)
    room = await db.scalar(
        select(LiveRoom).where(LiveRoom.id == room_id, LiveRoom.status == LiveRoomStatus.live)
    )
    participant = await db.scalar(
        select(LiveParticipant.id).where(
            LiveParticipant.live_room_id == room_id,
            LiveParticipant.user_id == buyer.id,
            LiveParticipant.left_at.is_(None),
        )
    )
    if room is None or participant is None:
        raise PermissionError("Live commerce requires active room membership")
    creator = await db.get(CreatorProfile, room.creator_id)
    if creator is None or creator.user_id == buyer.id:
        raise PermissionError("Live commerce is unavailable")
    return room, creator, await settings_for_creator(db, creator.id)


async def initiate_live_tip(
    db: AsyncSession,
    buyer: User,
    room_id: UUID,
    idempotency_key: str,
    *,
    amount_minor: int | None = None,
    tip_menu_item_id: UUID | None = None,
    compliance_decision: ComplianceDecision | None = None,
) -> LiveCommerceCharge:
    """Create one server-priced tip payment attempt; success is webhook-only."""

    if not idempotency_key or len(idempotency_key) > 100:
        raise StreamingError("A bounded Idempotency-Key is required")
    room, creator, settings = await _live_commerce_room(db, buyer, room_id, compliance_decision)
    locked = await finance.lock_payment_idempotency(db, buyer.id, f"live-tip:{idempotency_key}")
    if locked:
        existing = await db.scalar(
            select(LiveCommerceCharge).where(LiveCommerceCharge.payment_attempt_id == locked.id)
        )
        if existing is not None:
            return existing
        raise StreamingError("Payment idempotency key is already in use")
    menu_item = None
    if tip_menu_item_id:
        menu_item = await db.scalar(
            select(LiveTipMenuItem).where(
                LiveTipMenuItem.id == tip_menu_item_id,
                LiveTipMenuItem.creator_id == creator.id,
                LiveTipMenuItem.enabled.is_(True),
            )
        )
        if menu_item is None:
            raise StreamingError("Tip menu item is unavailable")
        amount = menu_item.amount_minor
        currency = currency_code(menu_item.currency)
    else:
        amount = amount_minor or 0
        currency = currency_code(settings.currency)
        if amount <= 0 or amount > settings.max_authorization_minor:
            raise StreamingError("Tip amount is outside the creator's permitted limit")
    if currency != currency_code(settings.currency):
        raise StreamingError("Tip currency is unavailable for this live room")
    attempt = PaymentAttempt(
        buyer_user_id=buyer.id,
        provider=get_settings().payment_provider,
        provider_reference=new_provider_reference(),
        amount_minor=amount,
        currency=currency,
        idempotency_key=f"live-tip:{idempotency_key}",
    )
    db.add(attempt)
    await db.flush()
    charge = LiveCommerceCharge(
        live_room_id=room.id,
        creator_id=creator.id,
        buyer_user_id=buyer.id,
        kind=LiveCommerceKind.tip,
        status=LiveCommerceStatus.pending_payment,
        tip_menu_item_id=menu_item.id if menu_item else None,
        gross_amount_minor=amount,
        currency=currency,
        commission_basis_points=await finance.commission_for(db, "tip"),
        payment_attempt_id=attempt.id,
    )
    db.add(charge)
    await db.flush()
    return charge


async def initiate_live_gift(
    db: AsyncSession,
    buyer: User,
    room_id: UUID,
    gift_catalog_item_id: UUID,
    idempotency_key: str,
    *,
    compliance_decision: ComplianceDecision | None = None,
) -> LiveCommerceCharge:
    room, creator, settings = await _live_commerce_room(db, buyer, room_id, compliance_decision)
    if not idempotency_key or len(idempotency_key) > 100:
        raise StreamingError("A bounded Idempotency-Key is required")
    gift = await db.scalar(
        select(LiveGiftCatalogItem).where(
            LiveGiftCatalogItem.id == gift_catalog_item_id, LiveGiftCatalogItem.active.is_(True)
        )
    )
    if gift is None or currency_code(gift.currency) != currency_code(settings.currency):
        raise StreamingError("Gift is unavailable for this live room")
    locked = await finance.lock_payment_idempotency(db, buyer.id, f"live-gift:{idempotency_key}")
    if locked:
        existing = await db.scalar(
            select(LiveCommerceCharge).where(LiveCommerceCharge.payment_attempt_id == locked.id)
        )
        if existing is not None:
            return existing
        raise StreamingError("Payment idempotency key is already in use")
    attempt = PaymentAttempt(
        buyer_user_id=buyer.id,
        provider=get_settings().payment_provider,
        provider_reference=new_provider_reference(),
        amount_minor=gift.amount_minor,
        currency=currency_code(gift.currency),
        idempotency_key=f"live-gift:{idempotency_key}",
    )
    db.add(attempt)
    await db.flush()
    charge = LiveCommerceCharge(
        live_room_id=room.id,
        creator_id=creator.id,
        buyer_user_id=buyer.id,
        kind=LiveCommerceKind.gift,
        status=LiveCommerceStatus.pending_payment,
        gift_catalog_item_id=gift.id,
        gross_amount_minor=gift.amount_minor,
        currency=currency_code(gift.currency),
        commission_basis_points=await finance.commission_for(db, "tip"),
        payment_attempt_id=attempt.id,
    )
    db.add(charge)
    await db.flush()
    return charge


async def initiate_live_paid_request(
    db: AsyncSession, buyer: User, room_id: UUID, option_id: UUID, message: str, idempotency_key: str,
    *, compliance_decision: ComplianceDecision | None = None,
) -> LiveCommerceCharge:
    room, creator, settings = await _live_commerce_room(db, buyer, room_id, compliance_decision)
    option = await db.scalar(select(LivePaidRequestOption).where(
        LivePaidRequestOption.id == option_id, LivePaidRequestOption.creator_id == creator.id,
        LivePaidRequestOption.enabled.is_(True),
    ))
    if (
        not idempotency_key
        or len(idempotency_key) > 100
        or option is None
        or currency_code(option.currency) != currency_code(settings.currency)
    ):
        raise StreamingError("Paid request is unavailable")
    normalized_message = message.strip()
    if not normalized_message or len(normalized_message) > 500:
        raise StreamingError("Paid request message is required and must be at most 500 characters")
    existing_attempt = await finance.lock_payment_idempotency(db, buyer.id, f"live-request:{idempotency_key}")
    if existing_attempt:
        existing = await db.scalar(select(LiveCommerceCharge).where(LiveCommerceCharge.payment_attempt_id == existing_attempt.id))
        if existing:
            return existing
        raise StreamingError("Payment idempotency key is already in use")
    attempt = PaymentAttempt(buyer_user_id=buyer.id, provider=get_settings().payment_provider,
        provider_reference=new_provider_reference(), amount_minor=option.amount_minor,
        currency=currency_code(option.currency), idempotency_key=f"live-request:{idempotency_key}")
    db.add(attempt); await db.flush()
    charge = LiveCommerceCharge(live_room_id=room.id, creator_id=creator.id, buyer_user_id=buyer.id,
        kind=LiveCommerceKind.paid_request, status=LiveCommerceStatus.pending_payment,
        paid_request_option_id=option.id, request_label=option.label, request_message=normalized_message,
        gross_amount_minor=option.amount_minor, currency=currency_code(option.currency),
        commission_basis_points=await finance.commission_for(db, "tip"), payment_attempt_id=attempt.id,
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        creator_acceptance_required=option.requires_creator_acceptance)
    db.add(charge); await db.flush()
    return charge


async def creator_paid_request_options(
    db: AsyncSession, actor: User
) -> list[LivePaidRequestOption]:
    creator = await approved_creator(db, actor)
    return list(
        await db.scalars(
            select(LivePaidRequestOption)
            .where(LivePaidRequestOption.creator_id == creator.id)
            .order_by(LivePaidRequestOption.sort_order, LivePaidRequestOption.created_at)
        )
    )


async def save_paid_request_option(
    db: AsyncSession,
    actor: User,
    *,
    label: str,
    amount_minor: int,
    enabled: bool,
    sort_order: int,
    requires_creator_acceptance: bool,
    option_id: UUID | None = None,
) -> LivePaidRequestOption:
    creator = await approved_creator(db, actor)
    settings = await settings_for_creator(db, creator.id)
    normalized_label = label.strip()
    if not normalized_label:
        raise StreamingError("Paid request label is required")
    if amount_minor <= 0 or amount_minor > settings.max_authorization_minor:
        raise StreamingError("Paid request amount is outside the creator's permitted limit")
    option = None
    if option_id:
        option = await db.scalar(
            select(LivePaidRequestOption)
            .where(
                LivePaidRequestOption.id == option_id,
                LivePaidRequestOption.creator_id == creator.id,
            )
            .with_for_update()
        )
        if option is None:
            raise PermissionError("Paid request option not found")
    else:
        option = LivePaidRequestOption(
            creator_id=creator.id,
            label=normalized_label,
            amount_minor=amount_minor,
            currency=currency_code(settings.currency),
        )
        db.add(option)
    option.label = normalized_label
    option.amount_minor = amount_minor
    option.currency = currency_code(settings.currency)
    option.enabled = enabled
    option.sort_order = sort_order
    option.requires_creator_acceptance = requires_creator_acceptance
    await db.flush()
    return option


async def room_paid_request_options(
    db: AsyncSession,
    actor: User,
    room_id: UUID,
    *,
    compliance_decision: ComplianceDecision | None = None,
) -> list[LivePaidRequestOption]:
    await require_live_compliance(db, actor, compliance_decision)
    room = await db.get(LiveRoom, room_id)
    membership = await db.scalar(
        select(LiveParticipant.id).where(
            LiveParticipant.live_room_id == room_id,
            LiveParticipant.user_id == actor.id,
            LiveParticipant.left_at.is_(None),
        )
    )
    if room is None or room.status is not LiveRoomStatus.live or membership is None:
        raise PermissionError("Paid requests require active room membership")
    return list(
        await db.scalars(
            select(LivePaidRequestOption)
            .where(
                LivePaidRequestOption.creator_id == room.creator_id,
                LivePaidRequestOption.enabled.is_(True),
            )
            .order_by(LivePaidRequestOption.sort_order, LivePaidRequestOption.created_at)
        )
    )


async def creator_pending_paid_requests(
    db: AsyncSession, actor: User
) -> list[LiveCommerceCharge]:
    creator = await approved_creator(db, actor)
    return list(
        await db.scalars(
            select(LiveCommerceCharge)
            .where(
                LiveCommerceCharge.creator_id == creator.id,
                LiveCommerceCharge.kind == LiveCommerceKind.paid_request,
                LiveCommerceCharge.status == LiveCommerceStatus.paid_pending_creator,
            )
            .order_by(LiveCommerceCharge.created_at)
        )
    )


async def creator_tip_menu(db: AsyncSession, actor: User) -> list[LiveTipMenuItem]:
    creator = await approved_creator(db, actor)
    return list(
        await db.scalars(
            select(LiveTipMenuItem)
            .where(LiveTipMenuItem.creator_id == creator.id)
            .order_by(LiveTipMenuItem.sort_order, LiveTipMenuItem.created_at)
        )
    )


async def save_tip_menu_item(
    db: AsyncSession,
    actor: User,
    *,
    label: str,
    amount_minor: int,
    enabled: bool,
    sort_order: int,
    item_id: UUID | None = None,
) -> LiveTipMenuItem:
    creator = await approved_creator(db, actor)
    settings = await settings_for_creator(db, creator.id)
    if amount_minor <= 0 or amount_minor > settings.max_authorization_minor:
        raise StreamingError("Tip menu amount is outside the creator's permitted limit")
    item = None
    if item_id:
        item = await db.scalar(
            select(LiveTipMenuItem)
            .where(LiveTipMenuItem.id == item_id, LiveTipMenuItem.creator_id == creator.id)
            .with_for_update()
        )
        if item is None:
            raise PermissionError("Tip menu item not found")
    else:
        item = LiveTipMenuItem(creator_id=creator.id, label="", amount_minor=1, currency="EUR")
        db.add(item)
    item.label = label.strip()
    item.amount_minor = amount_minor
    item.currency = currency_code(settings.currency)
    item.enabled = enabled
    item.sort_order = sort_order
    await db.flush()
    return item


async def creator_live_goals(db: AsyncSession, actor: User) -> list[LiveGoal]:
    creator = await approved_creator(db, actor)
    return list(
        await db.scalars(
            select(LiveGoal)
            .where(LiveGoal.creator_id == creator.id)
            .order_by(LiveGoal.active.desc(), LiveGoal.created_at.desc())
        )
    )


async def create_live_goal(
    db: AsyncSession, actor: User, *, title: str, target_amount_minor: int
) -> LiveGoal:
    creator = await approved_creator(db, actor)
    settings = await settings_for_creator(db, creator.id)
    goal = LiveGoal(
        creator_id=creator.id,
        title=title.strip(),
        target_amount_minor=target_amount_minor,
        currency=currency_code(settings.currency),
    )
    db.add(goal)
    await db.flush()
    return goal


async def live_goal_progress(
    db: AsyncSession, actor: User, room_id: UUID
) -> list[tuple[LiveGoal, int]]:
    room = await db.get(LiveRoom, room_id)
    membership = await db.scalar(
        select(LiveParticipant.id).where(
            LiveParticipant.live_room_id == room_id,
            LiveParticipant.user_id == actor.id,
            LiveParticipant.left_at.is_(None),
        )
    )
    if room is None or membership is None:
        raise PermissionError("Live goals require active room membership")
    goals = list(
        await db.scalars(
            select(LiveGoal).where(
                LiveGoal.creator_id == room.creator_id, LiveGoal.active.is_(True)
            )
        )
    )
    rows: list[tuple[LiveGoal, int]] = []
    for goal in goals:
        progress = await db.scalar(
            select(func.coalesce(func.sum(LiveCommerceCharge.gross_amount_minor), 0)).where(
                LiveCommerceCharge.live_room_id == room.id,
                LiveCommerceCharge.creator_id == room.creator_id,
                LiveCommerceCharge.currency == goal.currency,
                LiveCommerceCharge.status == LiveCommerceStatus.completed,
            )
        )
        rows.append((goal, int(progress or 0)))
    return rows


async def add_live_reaction(
    db: AsyncSession,
    actor: User,
    room_id: UUID,
    reaction_type: str,
    *,
    compliance_decision: ComplianceDecision | None = None,
) -> dict[str, int]:
    """Increment one bounded aggregate; never persist individual reaction spam."""

    await require_live_compliance(db, actor, compliance_decision)
    try:
        normalized = LiveReactionType(reaction_type)
    except ValueError as exc:
        raise StreamingError("Unsupported Live reaction") from exc
    member = await db.scalar(
        select(LiveParticipant.id)
        .join(LiveRoom, LiveRoom.id == LiveParticipant.live_room_id)
        .where(
            LiveParticipant.live_room_id == room_id,
            LiveParticipant.user_id == actor.id,
            LiveParticipant.left_at.is_(None),
            LiveRoom.status == LiveRoomStatus.live,
        )
    )
    if member is None:
        raise PermissionError("Live reactions require active room membership")
    statement = insert(LiveReactionAggregate).values(
        live_room_id=room_id,
        reaction_type=normalized,
        reaction_count=1,
    )
    statement = statement.on_conflict_do_update(
        constraint="uq_live_reaction_aggregate_room_type",
        set_={
            "reaction_count": LiveReactionAggregate.reaction_count + 1,
            "updated_at": datetime.now(UTC),
        },
    )
    await db.execute(statement)
    return await live_reaction_summary(
        db, actor, room_id, compliance_decision=compliance_decision
    )


async def live_reaction_summary(
    db: AsyncSession,
    actor: User,
    room_id: UUID,
    *,
    compliance_decision: ComplianceDecision | None = None,
) -> dict[str, int]:
    await require_live_compliance(db, actor, compliance_decision)
    member = await db.scalar(
        select(LiveParticipant.id)
        .join(LiveRoom, LiveRoom.id == LiveParticipant.live_room_id)
        .where(
            LiveParticipant.live_room_id == room_id,
            LiveParticipant.user_id == actor.id,
            LiveParticipant.left_at.is_(None),
            LiveRoom.status == LiveRoomStatus.live,
        )
    )
    if member is None:
        raise PermissionError("Live reactions require active room membership")
    rows = (
        await db.execute(
            select(
                LiveReactionAggregate.reaction_type,
                LiveReactionAggregate.reaction_count,
            ).where(LiveReactionAggregate.live_room_id == room_id)
        )
    ).all()
    return {reaction.value: count for reaction, count in rows}


async def live_supporter_ranking(
    db: AsyncSession,
    actor: User,
    room_id: UUID,
    *,
    compliance_decision: ComplianceDecision | None = None,
    limit: int = 10,
) -> list[dict[str, object]]:
    """Derive current-room rankings only from unreversed canonical settlements."""

    await require_live_compliance(db, actor, compliance_decision)
    member = await db.scalar(
        select(LiveParticipant.id)
        .join(LiveRoom, LiveRoom.id == LiveParticipant.live_room_id)
        .where(
            LiveParticipant.live_room_id == room_id,
            LiveParticipant.user_id == actor.id,
            LiveParticipant.left_at.is_(None),
            LiveRoom.status == LiveRoomStatus.live,
        )
    )
    if member is None:
        raise PermissionError("Supporter rankings require active room membership")
    reversal_exists = exists().where(
        LedgerTransaction.reversal_of_transaction_id == LiveEvent.ledger_transaction_id,
        LedgerTransaction.transaction_type.in_(
            [LedgerTransactionType.refund, LedgerTransactionType.chargeback]
        ),
    )
    rows = (
        await db.execute(
            select(
                LiveEvent.actor_user_id,
                LiveEvent.currency,
                func.sum(LiveEvent.amount_minor).label("amount_minor"),
            )
            .where(
                LiveEvent.live_room_id == room_id,
                LiveEvent.event_type.in_(["tip", "gift", "paid_request"]),
                LiveEvent.actor_user_id.is_not(None),
                LiveEvent.ledger_transaction_id.is_not(None),
                LiveEvent.amount_minor.is_not(None),
                ~reversal_exists,
            )
            .group_by(LiveEvent.actor_user_id, LiveEvent.currency)
            .order_by(func.sum(LiveEvent.amount_minor).desc(), LiveEvent.actor_user_id)
            .limit(max(1, min(limit, 25)))
        )
    ).all()
    return [
        {
            "rank": index,
            "amount_minor": int(row.amount_minor),
            "currency": row.currency,
            "supporter_label": "You" if row.actor_user_id == actor.id else f"Supporter {index}",
            "viewer_is_current_user": row.actor_user_id == actor.id,
        }
        for index, row in enumerate(rows, start=1)
    ]


async def settle_live_commerce_charge(
    db: AsyncSession, payment_attempt: PaymentAttempt
) -> LiveCommerceCharge | None:
    """Apply one verified payment without letting it bypass the request lifecycle."""

    charge = await db.scalar(
        select(LiveCommerceCharge)
        .where(LiveCommerceCharge.payment_attempt_id == payment_attempt.id)
        .with_for_update()
    )
    if charge is None:
        return None
    if charge.kind is LiveCommerceKind.paid_request:
        if payment_attempt.status is not PaymentStatus.succeeded:
            raise StreamingError("Live commerce payment is not confirmed")
        if charge.status in {LiveCommerceStatus.declined, LiveCommerceStatus.expired}:
            await finance.record_excess_capture(
                db,
                payment_attempt,
                source_type=ExcessCaptureSource.live_paid_request,
                source_reference=charge.id,
            )
            return charge
        if charge.ledger_transaction_id:
            return charge
        if charge.expires_at and charge.expires_at <= datetime.now(UTC):
            await _expire_paid_request(db, charge, payment_attempt)
            return charge
        if charge.creator_acceptance_required:
            if charge.status is LiveCommerceStatus.pending_payment:
                charge.status = LiveCommerceStatus.paid_pending_creator
                await record_live_event(
                    db,
                    event_type="paid_request_pending",
                    live_room_id=charge.live_room_id,
                    actor_user_id=charge.buyer_user_id,
                    source_type="live_commerce_charge",
                    source_id=str(charge.id),
                    idempotency_key=f"live-paid-request-pending:{charge.id}",
                    metadata={"request_label": charge.request_label or "Paid request"},
                )
            return charge
        await _complete_live_commerce_charge(db, charge)
        return charge
    await _complete_live_commerce_charge(db, charge)
    return charge


async def _complete_live_commerce_charge(
    db: AsyncSession, charge: LiveCommerceCharge
) -> LiveCommerceCharge:
    """Post the single immutable settlement and its single financial Live event."""

    if charge.ledger_transaction_id:
        return charge
    payment_attempt = await db.get(PaymentAttempt, charge.payment_attempt_id)
    if payment_attempt is None or payment_attempt.status is not PaymentStatus.succeeded:
        raise StreamingError("Live commerce payment is not confirmed")
    fee, creator_pool = finance.commission_amount(
        charge.gross_amount_minor, charge.commission_basis_points
    )
    clearing = await finance._account(db, LedgerAccountKind.platform_clearing, charge.currency)
    revenue = await finance._account(db, LedgerAccountKind.platform_revenue, charge.currency)
    allocation_entries, allocation_metadata = await finance.creator_revenue_allocation(
        db, charge.creator_id, charge.currency, creator_pool, charge.created_at
    )
    transaction_type = {
        LiveCommerceKind.tip: LedgerTransactionType.live_tip,
        LiveCommerceKind.gift: LedgerTransactionType.live_gift,
        LiveCommerceKind.paid_request: LedgerTransactionType.live_paid_request,
    }[charge.kind]
    ledger = await finance.post_entries(
        db,
        transaction_type=transaction_type,
        currency=charge.currency,
        idempotency_key=f"live-commerce:{charge.id}",
        reference=f"live-commerce:{charge.id}",
        entries=[
            (clearing, LedgerDirection.debit, charge.gross_amount_minor),
            (revenue, LedgerDirection.credit, fee),
            *allocation_entries,
        ],
        metadata={
            "live_commerce_charge_id": str(charge.id),
            "live_room_id": str(charge.live_room_id),
            "kind": charge.kind.value,
            "platform_fee_minor": str(fee),
            **allocation_metadata,
        },
    )
    charge.ledger_transaction_id = ledger.id
    charge.status = LiveCommerceStatus.completed
    charge.resolved_at = datetime.now(UTC)
    event_type = charge.kind.value
    metadata: dict[str, str] = {}
    if charge.tip_menu_item_id:
        menu = await db.get(LiveTipMenuItem, charge.tip_menu_item_id)
        if menu:
            metadata["tip_menu_label"] = menu.label
    if charge.gift_catalog_item_id:
        gift = await db.get(LiveGiftCatalogItem, charge.gift_catalog_item_id)
        if gift:
            metadata.update({"gift_name": gift.name, "gift_icon": gift.icon})
    if charge.kind is LiveCommerceKind.paid_request:
        metadata["request_label"] = charge.request_label or "Paid request"
    await record_live_event(
        db,
        event_type=event_type,
        live_room_id=charge.live_room_id,
        actor_user_id=charge.buyer_user_id,
        ledger_transaction_id=ledger.id,
        source_type="live_commerce_charge",
        source_id=str(charge.id),
        amount_minor=charge.gross_amount_minor,
        currency=charge.currency,
        idempotency_key=f"live-commerce-event:{charge.id}",
        metadata=metadata,
    )
    goals = list(
        await db.scalars(
            select(LiveGoal).where(
                LiveGoal.creator_id == charge.creator_id,
                LiveGoal.currency == charge.currency,
                LiveGoal.active.is_(True),
                LiveGoal.completed_at.is_(None),
            )
        )
    )
    for goal in goals:
        progress = await db.scalar(
            select(func.coalesce(func.sum(LiveCommerceCharge.gross_amount_minor), 0)).where(
                LiveCommerceCharge.live_room_id == charge.live_room_id,
                LiveCommerceCharge.creator_id == charge.creator_id,
                LiveCommerceCharge.currency == charge.currency,
                LiveCommerceCharge.status == LiveCommerceStatus.completed,
            )
        )
        if int(progress or 0) >= goal.target_amount_minor:
            goal.completed_at = datetime.now(UTC)
            await record_live_event(
                db,
                event_type="goal_completed",
                live_room_id=charge.live_room_id,
                source_type="live_goal",
                source_id=str(goal.id),
                idempotency_key=f"live-goal-completed:{goal.id}",
                metadata={"title": goal.title},
            )
    await record_event(
        db,
        f"live.{event_type}.settled",
        actor_user_id=charge.buyer_user_id,
        target_type="live_commerce_charge",
        target_id=str(charge.id),
        metadata={"ledger_transaction_id": str(ledger.id)},
    )
    return charge


async def accept_paid_request(
    db: AsyncSession, actor: User, charge_id: UUID
) -> LiveCommerceCharge:
    creator = await approved_creator(db, actor)
    charge = await db.scalar(
        select(LiveCommerceCharge)
        .where(
            LiveCommerceCharge.id == charge_id,
            LiveCommerceCharge.creator_id == creator.id,
            LiveCommerceCharge.kind == LiveCommerceKind.paid_request,
        )
        .with_for_update()
    )
    if charge is None:
        raise PermissionError("Paid request not found")
    if charge.status is LiveCommerceStatus.completed:
        return charge
    if charge.status is not LiveCommerceStatus.paid_pending_creator:
        raise StreamingError("Paid request is not awaiting creator acceptance")
    attempt = await db.get(PaymentAttempt, charge.payment_attempt_id)
    if attempt is None or attempt.status is not PaymentStatus.succeeded:
        raise StreamingError("Paid request payment is not confirmed")
    if charge.expires_at and charge.expires_at <= datetime.now(UTC):
        await _expire_paid_request(db, charge, attempt)
        raise StreamingError("Paid request has expired")
    return await _complete_live_commerce_charge(db, charge)


async def decline_paid_request(
    db: AsyncSession, actor: User, charge_id: UUID
) -> LiveCommerceCharge:
    creator = await approved_creator(db, actor)
    charge = await db.scalar(
        select(LiveCommerceCharge)
        .where(
            LiveCommerceCharge.id == charge_id,
            LiveCommerceCharge.creator_id == creator.id,
            LiveCommerceCharge.kind == LiveCommerceKind.paid_request,
        )
        .with_for_update()
    )
    if charge is None:
        raise PermissionError("Paid request not found")
    if charge.status is LiveCommerceStatus.declined:
        return charge
    if charge.status is not LiveCommerceStatus.paid_pending_creator:
        raise StreamingError("Paid request is not awaiting creator resolution")
    attempt = await db.get(PaymentAttempt, charge.payment_attempt_id)
    if attempt is None or attempt.status is not PaymentStatus.succeeded:
        raise StreamingError("Paid request payment is not confirmed")
    await finance.record_excess_capture(
        db,
        attempt,
        source_type=ExcessCaptureSource.live_paid_request,
        source_reference=charge.id,
    )
    charge.status = LiveCommerceStatus.declined
    charge.resolved_at = datetime.now(UTC)
    await record_live_event(
        db,
        event_type="paid_request_declined",
        live_room_id=charge.live_room_id,
        actor_user_id=actor.id,
        source_type="live_commerce_charge",
        source_id=str(charge.id),
        idempotency_key=f"live-paid-request-declined:{charge.id}",
        metadata={"request_label": charge.request_label or "Paid request"},
    )
    return charge


async def _expire_paid_request(
    db: AsyncSession,
    charge: LiveCommerceCharge,
    attempt: PaymentAttempt | None,
) -> LiveCommerceCharge:
    if charge.status is LiveCommerceStatus.expired:
        if attempt and attempt.status is PaymentStatus.succeeded:
            await finance.record_excess_capture(
                db,
                attempt,
                source_type=ExcessCaptureSource.live_paid_request,
                source_reference=charge.id,
            )
        return charge
    if charge.status not in {
        LiveCommerceStatus.pending_payment,
        LiveCommerceStatus.paid_pending_creator,
    }:
        return charge
    if attempt and attempt.status is PaymentStatus.succeeded:
        await finance.record_excess_capture(
            db,
            attempt,
            source_type=ExcessCaptureSource.live_paid_request,
            source_reference=charge.id,
        )
    charge.status = LiveCommerceStatus.expired
    charge.resolved_at = datetime.now(UTC)
    await record_live_event(
        db,
        event_type="paid_request_expired",
        live_room_id=charge.live_room_id,
        source_type="live_commerce_charge",
        source_id=str(charge.id),
        idempotency_key=f"live-paid-request-expired:{charge.id}",
        metadata={"request_label": charge.request_label or "Paid request"},
    )
    return charge


async def expire_paid_requests(
    db: AsyncSession, *, now: datetime | None = None, limit: int = 100
) -> int:
    current = now or datetime.now(UTC)
    rows = list(
        await db.scalars(
            select(LiveCommerceCharge)
            .where(
                LiveCommerceCharge.kind == LiveCommerceKind.paid_request,
                LiveCommerceCharge.status.in_(
                    [
                        LiveCommerceStatus.pending_payment,
                        LiveCommerceStatus.paid_pending_creator,
                    ]
                ),
                LiveCommerceCharge.expires_at <= current,
            )
            .order_by(LiveCommerceCharge.expires_at, LiveCommerceCharge.id)
            .limit(max(1, min(limit, 500)))
            .with_for_update(skip_locked=True)
        )
    )
    for charge in rows:
        await _expire_paid_request(
            db, charge, await db.get(PaymentAttempt, charge.payment_attempt_id)
        )
    return len(rows)


async def fail_live_commerce_charge(
    db: AsyncSession, payment_attempt: PaymentAttempt
) -> LiveCommerceCharge | None:
    """Make a failed provider attempt terminal without emitting a success event."""

    charge = await db.scalar(
        select(LiveCommerceCharge)
        .where(LiveCommerceCharge.payment_attempt_id == payment_attempt.id)
        .with_for_update()
    )
    if charge and charge.status is LiveCommerceStatus.pending_payment:
        charge.status = LiveCommerceStatus.expired
        charge.resolved_at = datetime.now(UTC)
    return charge


async def reverse_live_commerce_charge(
    db: AsyncSession,
    payment_attempt: PaymentAttempt,
    *,
    resolution_type: LedgerTransactionType,
    provider_event_id: str,
) -> LiveCommerceCharge | None:
    """Post one compensating reversal; never edit the original Live settlement."""

    charge = await db.scalar(
        select(LiveCommerceCharge)
        .where(LiveCommerceCharge.payment_attempt_id == payment_attempt.id)
        .with_for_update()
    )
    if charge is None:
        return None
    if charge.ledger_transaction_id is None:
        charge.status = LiveCommerceStatus.disputed
        charge.resolved_at = datetime.now(UTC)
        return charge
    original = await db.get(LedgerTransaction, charge.ledger_transaction_id)
    if original is None:
        raise StreamingError("Live commerce settlement is missing")
    reversal = await finance.reverse_original_ledger(
        db,
        original.id,
        transaction_type=resolution_type,
        idempotency_key=f"provider-reversal:live-commerce:{charge.id}",
        reference=f"provider_reversal:live_commerce:{charge.id}",
        metadata={
            "live_commerce_charge_id": str(charge.id),
            "provider_event_id": provider_event_id,
        },
    )
    charge.status = (
        LiveCommerceStatus.refunded
        if resolution_type is LedgerTransactionType.refund
        else LiveCommerceStatus.disputed
    )
    charge.resolved_at = datetime.now(UTC)
    await record_live_event(
        db,
        event_type="commerce_reversed",
        live_room_id=charge.live_room_id,
        source_type="live_commerce_charge",
        source_id=str(charge.id),
        amount_minor=charge.gross_amount_minor,
        currency=charge.currency,
        idempotency_key=f"live-commerce-reversal-event:{charge.id}:{resolution_type.value}",
        metadata={"reversal_ledger_transaction_id": str(reversal.id)},
    )
    return charge


async def current_creator_public_live_room(db: AsyncSession, actor: User) -> LiveRoom | None:
    """Return only the caller's current public room for Studio recovery.

    A browser refresh must not make an already-started room look startable again.
    This owner-only query is deliberately separate from the public live discovery
    projection: it includes an ``ending`` room so its owner sees the pending
    termination rather than accidentally attempting to start another one.
    """

    return await db.scalar(
        select(LiveRoom)
        .join(CreatorProfile, CreatorProfile.id == LiveRoom.creator_id)
        .where(
            CreatorProfile.user_id == actor.id,
            LiveRoom.status.in_(
                [LiveRoomStatus.starting, LiveRoomStatus.live, LiveRoomStatus.ending]
            ),
        )
        .order_by(LiveRoom.started_at.desc())
    )


async def start_live(
    db: AsyncSession,
    actor: User,
    title: str,
    access_mode: LiveAccessMode,
    description: str | None = None,
    *,
    compliance_decision: ComplianceDecision | None = None,
) -> LiveRoom:
    await lock_compliance_subject(db, actor.id)
    await require_live_compliance(db, actor, compliance_decision)
    # Serialize every public/private live creation decision on the creator row.
    # A FOR UPDATE query over an empty active-set does not protect the
    # non-existence invariant in PostgreSQL.
    creator = await db.scalar(
        select(CreatorProfile)
        .where(CreatorProfile.user_id == actor.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if creator is None or creator.status is not CreatorStatus.approved or not creator.is_public:
        raise PermissionError("An approved public creator profile is required")
    creator_eligibility = await resolve_creator_compliance_eligibility(db, profile=creator)
    if not creator_eligibility.public_allowed:
        raise PermissionError(creator_eligibility.reason)
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
    active_private = await db.scalar(
        select(PrivateSession.id).where(
            PrivateSession.creator_id == creator.id,
            PrivateSession.status.in_(
                [
                    PrivateSessionStatus.awaiting_payment_authorization,
                    PrivateSessionStatus.ready,
                    PrivateSessionStatus.connecting,
                    PrivateSessionStatus.active,
                    PrivateSessionStatus.reconnecting,
                    PrivateSessionStatus.ending,
                ]
            ),
        )
    )
    if active_private:
        raise StreamingError("End the private live before starting a public room")
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


async def _mark_public_room_ended(
    db: AsyncSession,
    room: LiveRoom,
    *,
    ended_at: datetime | None = None,
) -> None:
    current = ended_at or datetime.now(UTC)
    room.status, room.ended_at, room.viewer_count = LiveRoomStatus.ended, current, 0
    participants = (
        await db.scalars(
            select(LiveParticipant).where(
                LiveParticipant.live_room_id == room.id,
                LiveParticipant.left_at.is_(None),
            )
        )
    ).all()
    for participant in participants:
        participant.left_at = current


async def _enqueue_public_room_termination(
    db: AsyncSession,
    room: LiveRoom,
    *,
    reason: str,
    actor_user_id: UUID | None = None,
    idempotency_suffix: str | None = None,
) -> bool:
    """Persist the deny-first state and its provider control in one transaction."""

    # The first transition atomically records both `ending` and its durable
    # provider command.  Later authority sweeps may supply a different
    # diagnostic reason, but must not replace that command or turn a retry
    # into an idempotency-key collision.
    if room.status is LiveRoomStatus.ending:
        return False
    terminal_reclose = room.status in {LiveRoomStatus.ended, LiveRoomStatus.failed}
    if not terminal_reclose:
        room.status = LiveRoomStatus.ending
    idempotency_key = (
        f"live-room-reclose:{room.id}:{room.ended_at.isoformat() if room.ended_at else 'terminal'}"
        if terminal_reclose
        else f"live-room-close:{room.id}"
    )
    if idempotency_suffix:
        idempotency_key = f"{idempotency_key}:{idempotency_suffix}"
    intent, created = await enqueue_live_provider_control_intent(
        db,
        action=LiveProviderControlAction.delete_room,
        target_type="live_room",
        target_id=str(room.id),
        provider_room_name=room.provider_room_name,
        reason=reason,
        actor_user_id=actor_user_id,
        idempotency_key=idempotency_key,
    )
    if created:
        await record_event(
            db,
            "live.termination_enqueued",
            actor_user_id=actor_user_id,
            target_type="live_room",
            target_id=str(room.id),
            metadata={"reason": reason, "intent_id": str(intent.id)},
        )
    return created


async def _enqueue_public_participant_eviction(
    db: AsyncSession,
    room: LiveRoom,
    participant: LiveParticipant,
    *,
    reason: str,
    actor_user_id: UUID | None = None,
) -> bool:
    """Queue provider removal while the domain authority already denies rejoin."""

    if participant.left_at is not None:
        return False
    intent, created = await enqueue_live_provider_control_intent(
        db,
        action=LiveProviderControlAction.remove_participant,
        target_type="live_room_participant",
        target_id=str(participant.id),
        provider_room_name=room.provider_room_name,
        participant_identity=str(participant.user_id),
        reason=reason,
        actor_user_id=actor_user_id,
        idempotency_key=f"live-room-participant-remove:{room.id}:{participant.user_id}",
    )
    if created:
        await record_event(
            db,
            "live.participant_eviction_enqueued",
            actor_user_id=actor_user_id,
            target_type="live_room",
            target_id=str(room.id),
            metadata={
                "user_id": str(participant.user_id),
                "reason": reason,
                "intent_id": str(intent.id),
            },
        )
    return created


async def finalize_live_provider_control_success(
    db: AsyncSession,
    intent: LiveProviderControlIntent,
) -> None:
    """Apply one successful LiveKit command exactly once under the outbox fence."""

    if intent.target_type == "live_room":
        if intent.action is not LiveProviderControlAction.delete_room:
            raise LiveProviderControlStructuralError("LIVE_ROOM_ACTION_INVALID")
        try:
            room_id = UUID(intent.target_id)
        except ValueError as exc:
            raise LiveProviderControlStructuralError("LIVE_ROOM_TARGET_INVALID") from exc
        room = await db.scalar(select(LiveRoom).where(LiveRoom.id == room_id).with_for_update())
        if room is None:
            raise LiveProviderControlStructuralError("LIVE_ROOM_TARGET_MISSING")
        if room.provider_room_name != intent.provider_room_name:
            raise LiveProviderControlStructuralError("LIVE_ROOM_PROVIDER_MISMATCH")
        if room.status is LiveRoomStatus.ending:
            await _mark_public_room_ended(db, room)
            await record_event(
                db,
                "live.termination_completed",
                actor_user_id=intent.actor_user_id,
                target_type="live_room",
                target_id=str(room.id),
                metadata={"reason": intent.reason, "intent_id": str(intent.id)},
            )
        return

    if intent.target_type == "live_room_participant":
        if intent.action is not LiveProviderControlAction.remove_participant:
            raise LiveProviderControlStructuralError("LIVE_PARTICIPANT_ACTION_INVALID")
        try:
            participant_id = UUID(intent.target_id)
            participant_user_id = UUID(intent.participant_identity or "")
        except ValueError as exc:
            raise LiveProviderControlStructuralError("LIVE_PARTICIPANT_TARGET_INVALID") from exc
        participant = await db.scalar(
            select(LiveParticipant).where(LiveParticipant.id == participant_id).with_for_update()
        )
        if participant is None:
            raise LiveProviderControlStructuralError("LIVE_PARTICIPANT_TARGET_MISSING")
        if participant.user_id != participant_user_id:
            raise LiveProviderControlStructuralError("LIVE_PARTICIPANT_IDENTITY_MISMATCH")
        room = await db.scalar(
            select(LiveRoom).where(LiveRoom.id == participant.live_room_id).with_for_update()
        )
        if room is None or room.provider_room_name != intent.provider_room_name:
            raise LiveProviderControlStructuralError("LIVE_PARTICIPANT_PROVIDER_MISMATCH")
        if participant.left_at is None:
            participant.left_at = datetime.now(UTC)
            if participant.role is LiveParticipantRole.viewer:
                room.viewer_count = max(0, room.viewer_count - 1)
        return

    if intent.target_type == "live_room_identity":
        if intent.action is not LiveProviderControlAction.remove_participant:
            raise LiveProviderControlStructuralError("LIVE_IDENTITY_ACTION_INVALID")
        try:
            room_id = UUID(intent.target_id)
            participant_user_id = UUID(intent.participant_identity or "")
        except ValueError as exc:
            raise LiveProviderControlStructuralError("LIVE_IDENTITY_TARGET_INVALID") from exc
        room = await db.scalar(select(LiveRoom).where(LiveRoom.id == room_id).with_for_update())
        if room is None or room.provider_room_name != intent.provider_room_name:
            raise LiveProviderControlStructuralError("LIVE_IDENTITY_PROVIDER_MISMATCH")
        participant = await db.scalar(
            select(LiveParticipant)
            .where(
                LiveParticipant.live_room_id == room.id,
                LiveParticipant.user_id == participant_user_id,
            )
            .with_for_update()
        )
        if participant is not None and participant.left_at is None:
            participant.left_at = datetime.now(UTC)
            if participant.role is LiveParticipantRole.viewer:
                room.viewer_count = max(0, room.viewer_count - 1)
        return

    if intent.target_type == "private_session":
        if intent.action is not LiveProviderControlAction.delete_room:
            raise LiveProviderControlStructuralError("PRIVATE_SESSION_ACTION_INVALID")
        try:
            session_id = UUID(intent.target_id)
        except ValueError as exc:
            raise LiveProviderControlStructuralError("PRIVATE_SESSION_TARGET_INVALID") from exc
        session = await db.scalar(
            select(PrivateSession).where(PrivateSession.id == session_id).with_for_update()
        )
        if session is None:
            raise LiveProviderControlStructuralError("PRIVATE_SESSION_TARGET_MISSING")
        if session.provider_room_name != intent.provider_room_name:
            raise LiveProviderControlStructuralError("PRIVATE_SESSION_PROVIDER_MISMATCH")
        if session.status is PrivateSessionStatus.ending:
            await end_private_session(
                db,
                None,
                session.id,
                session.end_reason or intent.reason,
                session.ended_at,
                provider_room_closed=True,
            )
        return

    raise LiveProviderControlStructuralError("LIVE_PROVIDER_CONTROL_TARGET_UNSUPPORTED")


async def end_live(db: AsyncSession, actor: User, room_id: UUID) -> LiveRoom:
    room = await db.scalar(select(LiveRoom).where(LiveRoom.id == room_id).with_for_update())
    if room is None or room.creator_id != (await approved_creator(db, actor)).id:
        raise PermissionError("Live room not found")
    if room.status is LiveRoomStatus.ended:
        return room
    if room.status is not LiveRoomStatus.live:
        raise StreamingError("Live room cannot be ended from its current state")
    # Commit ``ending`` and the outbox command before LiveKit is contacted.
    # The worker owns provider I/O and finalizes this row only after success.
    await _enqueue_public_room_termination(
        db, room, reason="ended_by_creator", actor_user_id=actor.id
    )
    return room


async def terminate_live_for_moderation(
    db: AsyncSession, actor: User, room_id: UUID, reason: str
) -> LiveRoom:
    """Trust & Safety entry point; preserves room history while ending active public delivery."""
    room = await db.scalar(select(LiveRoom).where(LiveRoom.id == room_id).with_for_update())
    if not room:
        raise PermissionError("Live room not found")
    if room.status is LiveRoomStatus.ended:
        return room
    if room.status is not LiveRoomStatus.live:
        raise StreamingError("Live room cannot be terminated from its current state")
    await _enqueue_public_room_termination(db, room, reason=reason, actor_user_id=actor.id)
    return room


async def can_join_live(
    db: AsyncSession,
    viewer: User,
    room: LiveRoom,
    compliance_decision: ComplianceDecision | None = None,
) -> bool:
    try:
        await require_live_compliance(db, viewer, compliance_decision)
    except PermissionError:
        return False
    if room.status is not LiveRoomStatus.live:
        return False
    creator = await db.get(CreatorProfile, room.creator_id)
    if creator is None or await is_blocked(db, viewer.id, creator.user_id):
        return False
    if not (await resolve_creator_compliance_eligibility(db, profile=creator)).public_allowed:
        return False
    if creator.status is not CreatorStatus.approved or not creator.is_public:
        return False
    if room.access_mode is LiveAccessMode.public:
        return True
    from app.models.social import Follow

    if room.access_mode is LiveAccessMode.followers:
        return bool(
            await db.scalar(
                select(Follow).where(
                    Follow.creator_id == room.creator_id, Follow.user_id == viewer.id
                )
            )
        )
    return (
        await _active_creator_entitlement(
            db,
            user_id=viewer.id,
            creator_id=room.creator_id,
        )
        is not None
    )


async def join_live(
    db: AsyncSession,
    viewer: User,
    room_id: UUID,
    *,
    compliance_decision: ComplianceDecision | None = None,
) -> LiveParticipant:
    # Serialize token/join authority with verification revoke/review/expiry.
    # This lock must precede the room lock everywhere a fresh provider
    # capability can be created.
    await lock_compliance_subject(db, viewer.id)
    decision = await require_live_compliance(db, viewer, compliance_decision)
    # Standard lock order for public presence is room -> participant. Provider
    # callbacks, bans, joins, and enforcement all follow this order.
    room = await db.scalar(select(LiveRoom).where(LiveRoom.id == room_id).with_for_update())
    if room is None or not await can_join_live(db, viewer, room, decision):
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


async def issue_live_token(
    db: AsyncSession,
    viewer: User,
    room_id: UUID,
    *,
    compliance_decision: ComplianceDecision | None = None,
) -> tuple[LiveRoom, str]:
    await lock_compliance_subject(db, viewer.id)
    decision = await require_live_compliance(db, viewer, compliance_decision)
    room = await db.get(LiveRoom, room_id)
    if room is None:
        raise PermissionError("Live room not found")
    participant = await join_live(db, viewer, room_id, compliance_decision=decision)
    creator = await db.get(CreatorProfile, room.creator_id)
    can_publish = bool(creator and creator.user_id == viewer.id)
    if creator is None:
        raise PermissionError("Live room is unavailable")
    creator_eligibility = await resolve_creator_compliance_eligibility(db, profile=creator)
    entitlement_expiry = None
    if room.access_mode is LiveAccessMode.subscribers and not can_publish:
        entitlement = await _active_creator_entitlement(
            db,
            user_id=viewer.id,
            creator_id=room.creator_id,
        )
        if entitlement is None:
            raise PermissionError("Live room is unavailable")
        entitlement_expiry = entitlement.valid_until
    token = await LiveKitStreamingProvider().participant_token(
        room.provider_room_name,
        str(viewer.id),
        can_publish=can_publish,
        can_subscribe=True,
        authority_expires_at=_authority_expiry(
            decision.verification_expires_at,
            # A route decision may carry a shorter trusted request-scoped
            # authority lifetime. It can never restore access (the decision
            # above is freshly re-resolved under the subject lock), but it
            # must still cap the issued bearer capability.
            compliance_decision.verification_expires_at if compliance_decision else None,
            (
                creator_eligibility.verification_expires_at
                if creator_eligibility.identity_required or creator_eligibility.age_required
                else None
            ),
            entitlement_expiry,
        ),
    )
    participant.left_at = None
    return room, token


async def post_chat(
    db: AsyncSession,
    actor: User,
    room_id: UUID,
    body: str,
    *,
    compliance_decision: ComplianceDecision | None = None,
) -> LiveChatMessage:
    await require_live_compliance(db, actor, compliance_decision)
    participant = await db.scalar(
        select(LiveParticipant)
        .join(LiveRoom, LiveRoom.id == LiveParticipant.live_room_id)
        .where(
            LiveParticipant.live_room_id == room_id,
            LiveParticipant.user_id == actor.id,
            LiveParticipant.left_at.is_(None),
            LiveRoom.status == LiveRoomStatus.live,
        )
    )
    if participant is None or not body.strip():
        raise PermissionError("Live chat requires active room membership")
    message = LiveChatMessage(
        live_room_id=room_id, sender_user_id=actor.id, kind=LiveChatKind.text, body=body.strip()
    )
    db.add(message)
    await db.flush()
    await record_live_event(
        db,
        event_type="chat_message",
        live_room_id=room_id,
        actor_user_id=actor.id,
        source_type="live_chat_message",
        source_id=str(message.id),
        idempotency_key=f"live-chat:{message.id}",
    )
    return message


async def live_chat_history(
    db: AsyncSession,
    actor: User,
    room_id: UUID,
    *,
    compliance_decision: ComplianceDecision | None = None,
) -> list[LiveChatMessage]:
    """Read chat without implicitly joining or mutating viewer counters."""
    await require_live_compliance(db, actor, compliance_decision)
    participant = await db.scalar(
        select(LiveParticipant)
        .join(LiveRoom, LiveRoom.id == LiveParticipant.live_room_id)
        .where(
            LiveParticipant.live_room_id == room_id,
            LiveParticipant.user_id == actor.id,
            LiveParticipant.left_at.is_(None),
            LiveRoom.status == LiveRoomStatus.live,
        )
    )
    if participant is None:
        raise PermissionError("Live chat requires active room membership")
    return list(
        await db.scalars(
            select(LiveChatMessage)
            .where(LiveChatMessage.live_room_id == room_id)
            .order_by(LiveChatMessage.created_at)
        )
    )


async def live_activity_history(
    db: AsyncSession,
    actor: User,
    room_id: UUID,
    *,
    compliance_decision: ComplianceDecision | None = None,
    limit: int = 100,
) -> list[LiveEvent]:
    """Return the durable, presentation-safe event stream after reconnect."""

    await require_live_compliance(db, actor, compliance_decision)
    participant = await db.scalar(
        select(LiveParticipant.id)
        .join(LiveRoom, LiveRoom.id == LiveParticipant.live_room_id)
        .where(
            LiveParticipant.live_room_id == room_id,
            LiveParticipant.user_id == actor.id,
            LiveParticipant.left_at.is_(None),
            LiveRoom.status == LiveRoomStatus.live,
        )
    )
    if participant is None:
        raise PermissionError("Live activity requires active room membership")
    return list(
        await db.scalars(
            select(LiveEvent)
            .where(
                LiveEvent.live_room_id == room_id,
                LiveEvent.presentation_hidden.is_(False),
            )
            .order_by(LiveEvent.occurred_at.desc(), LiveEvent.id.desc())
            .limit(max(1, min(limit, 100)))
        )
    )[::-1]


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
    created = ban is None
    if ban is None:
        ban = LiveBan(
            live_room_id=room_id, user_id=viewer_id, actor_user_id=actor.id, reason=reason
        )
        db.add(ban)
    participant = await db.scalar(
        select(LiveParticipant)
        .where(
            LiveParticipant.live_room_id == room_id,
            LiveParticipant.user_id == viewer_id,
        )
        .with_for_update()
    )
    if participant is not None and participant.left_at is None:
        await _enqueue_public_participant_eviction(
            db,
            room,
            participant,
            reason="live_viewer_banned",
            actor_user_id=actor.id,
        )
    if created:
        await record_event(
            db,
            "live.viewer_banned",
            actor_user_id=actor.id,
            target_type="live_room",
            target_id=str(room_id),
            metadata={"viewer_user_id": str(viewer_id), "reason": reason},
        )
    return ban


async def remove_live_participant_for_moderation(
    db: AsyncSession,
    actor: User,
    room_id: UUID,
    viewer_id: UUID,
    reason: str,
) -> LiveParticipant:
    """T&S command that commits a durable LiveKit removal before provider I/O."""

    room = await db.scalar(select(LiveRoom).where(LiveRoom.id == room_id).with_for_update())
    if room is None or room.status not in {LiveRoomStatus.live, LiveRoomStatus.ending}:
        raise PermissionError("Live room not found")
    creator_user_id = await db.scalar(
        select(CreatorProfile.user_id).where(CreatorProfile.id == room.creator_id)
    )
    if viewer_id == creator_user_id:
        raise StreamingError("Terminate the Live room to remove its creator")
    participant = await db.scalar(
        select(LiveParticipant)
        .where(
            LiveParticipant.live_room_id == room_id,
            LiveParticipant.user_id == viewer_id,
        )
        .with_for_update()
    )
    if participant is None:
        raise PermissionError("Live participant not found")
    if participant.left_at is None:
        await _enqueue_public_participant_eviction(
            db,
            room,
            participant,
            reason="trust_safety_participant_removal",
            actor_user_id=actor.id,
        )
    await record_event(
        db,
        "live.participant_removed_by_moderation",
        actor_user_id=actor.id,
        target_type="live_room",
        target_id=str(room_id),
        metadata={"viewer_user_id": str(viewer_id), "reason": reason},
    )
    return participant


async def _record_livekit_control_failure(
    db: AsyncSession,
    *,
    operation: str,
    target_type: str,
    target_id: UUID,
    user_id: UUID | None = None,
) -> None:
    await record_event(
        db,
        "live.provider_control_failed",
        target_type=target_type,
        target_id=str(target_id),
        metadata={
            "operation": operation,
            "user_id": str(user_id) if user_id else None,
            "retry_required": True,
        },
    )


async def _end_public_room_for_authority(
    db: AsyncSession,
    room: LiveRoom,
    *,
    reason: str,
) -> bool:
    if room.status not in {LiveRoomStatus.live, LiveRoomStatus.ending}:
        return False
    return await _enqueue_public_room_termination(db, room, reason=reason)


async def _evict_public_participant_for_authority(
    db: AsyncSession,
    room: LiveRoom,
    participant: LiveParticipant,
    *,
    reason: str,
) -> bool:
    if participant.left_at is not None:
        return False
    return await _enqueue_public_participant_eviction(db, room, participant, reason=reason)


async def evict_user_from_active_live(
    db: AsyncSession,
    user_id: UUID,
    *,
    reason: str = "compliance_authority_revoked",
    force: bool = False,
) -> int:
    """Best-effort immediate eviction after an authority lifecycle change.

    A provider failure is audited and leaves the product state active so the
    scheduled reconciliation can retry. The compliance revocation transaction
    itself is never rolled back merely because LiveKit is unavailable.
    """

    user = await db.get(User, user_id)
    if user is None:
        return 0
    if not force:
        try:
            await require_live_compliance(db, user)
        except PermissionError:
            pass
        else:
            return 0

    affected = 0
    public_targets = (
        await db.execute(
            select(LiveParticipant.live_room_id, LiveParticipant.id)
            .join(LiveRoom, LiveRoom.id == LiveParticipant.live_room_id)
            .where(
                LiveParticipant.user_id == user_id,
                LiveParticipant.left_at.is_(None),
                LiveRoom.status.in_([LiveRoomStatus.live, LiveRoomStatus.ending]),
            )
            .order_by(LiveParticipant.live_room_id, LiveParticipant.id)
        )
    ).all()
    for room_id, participant_id in public_targets:
        room = await db.scalar(select(LiveRoom).where(LiveRoom.id == room_id).with_for_update())
        if room is None or room.status not in {
            LiveRoomStatus.live,
            LiveRoomStatus.ending,
        }:
            continue
        participant = await db.scalar(
            select(LiveParticipant)
            .where(
                LiveParticipant.id == participant_id,
                LiveParticipant.left_at.is_(None),
            )
            .with_for_update()
        )
        if participant is None:
            continue
        creator = await db.get(CreatorProfile, room.creator_id)
        if creator and creator.user_id == user_id:
            affected += int(await _end_public_room_for_authority(db, room, reason=reason))
        else:
            affected += int(
                await _evict_public_participant_for_authority(
                    db,
                    room,
                    participant,
                    reason=reason,
                )
            )

    private_sessions = (
        await db.scalars(
            select(PrivateSession)
            .join(
                SessionParticipant,
                SessionParticipant.private_session_id == PrivateSession.id,
            )
            .where(
                SessionParticipant.user_id == user_id,
                PrivateSession.status.in_(
                    [
                        PrivateSessionStatus.ready,
                        PrivateSessionStatus.connecting,
                        PrivateSessionStatus.active,
                        PrivateSessionStatus.reconnecting,
                        PrivateSessionStatus.ending,
                    ]
                ),
            )
        )
    ).all()
    for session in private_sessions:
        try:
            ended_session = await end_private_session(db, None, session.id, reason)
        except StreamingProviderError:
            await _record_livekit_control_failure(
                db,
                operation="delete_room",
                target_type="private_session",
                target_id=session.id,
                user_id=user_id,
            )
        else:
            if ended_session.status is not PrivateSessionStatus.ending:
                affected += 1
                await record_event(
                    db,
                    "private_session.authority_terminated",
                    target_type="private_session",
                    target_id=str(session.id),
                    metadata={"user_id": str(user_id), "reason": reason},
                )
    return affected


async def enforce_user_block_on_active_live(
    db: AsyncSession,
    first_user_id: UUID,
    second_user_id: UUID,
) -> int:
    """Immediately remove delivery shared by a newly blocked user pair."""

    if first_user_id == second_user_id:
        return 0
    affected = 0
    pair = {first_user_id, second_user_id}
    public_rows = (
        await db.execute(
            select(LiveRoom.id, CreatorProfile.user_id, LiveParticipant.id)
            .join(CreatorProfile, CreatorProfile.id == LiveRoom.creator_id)
            .join(LiveParticipant, LiveParticipant.live_room_id == LiveRoom.id)
            .where(
                LiveRoom.status.in_([LiveRoomStatus.live, LiveRoomStatus.ending]),
                CreatorProfile.user_id.in_(pair),
                LiveParticipant.user_id.in_(pair),
                LiveParticipant.user_id != CreatorProfile.user_id,
                LiveParticipant.left_at.is_(None),
            )
            .order_by(LiveRoom.id, LiveParticipant.id)
        )
    ).all()
    for room_id, _creator_user_id, participant_id in public_rows:
        room = await db.scalar(select(LiveRoom).where(LiveRoom.id == room_id).with_for_update())
        participant = await db.scalar(
            select(LiveParticipant)
            .where(
                LiveParticipant.id == participant_id,
                LiveParticipant.left_at.is_(None),
            )
            .with_for_update()
        )
        if room is None or participant is None:
            continue
        affected += int(
            await _evict_public_participant_for_authority(
                db,
                room,
                participant,
                reason="user_block_created",
            )
        )

    first_participation = select(SessionParticipant.private_session_id).where(
        SessionParticipant.user_id == first_user_id
    )
    second_participation = select(SessionParticipant.private_session_id).where(
        SessionParticipant.user_id == second_user_id
    )
    private_sessions = (
        await db.scalars(
            select(PrivateSession).where(
                PrivateSession.id.in_(first_participation),
                PrivateSession.id.in_(second_participation),
                PrivateSession.status.in_(
                    [
                        PrivateSessionStatus.ready,
                        PrivateSessionStatus.connecting,
                        PrivateSessionStatus.active,
                        PrivateSessionStatus.reconnecting,
                        PrivateSessionStatus.ending,
                    ]
                ),
            )
        )
    ).all()
    for session in private_sessions:
        ended_session = await end_private_session(
            db,
            None,
            session.id,
            "user_block_created",
        )
        if ended_session.status is not PrivateSessionStatus.ending:
            affected += 1
    return affected


async def terminate_creator_active_live(
    db: AsyncSession,
    creator_id: UUID,
    *,
    reason: str,
) -> int:
    """Best-effort provider enforcement after creator KYC/status revocation."""

    affected = 0
    room_ids = (
        await db.scalars(
            select(LiveRoom.id)
            .where(
                LiveRoom.creator_id == creator_id,
                LiveRoom.status.in_([LiveRoomStatus.live, LiveRoomStatus.ending]),
            )
            .order_by(LiveRoom.id)
        )
    ).all()
    for room_id in room_ids:
        room = await db.scalar(select(LiveRoom).where(LiveRoom.id == room_id).with_for_update())
        if room is None:
            continue
        affected += int(await _end_public_room_for_authority(db, room, reason=reason))
    sessions = (
        await db.scalars(
            select(PrivateSession).where(
                PrivateSession.creator_id == creator_id,
                PrivateSession.status.in_(
                    [
                        PrivateSessionStatus.ready,
                        PrivateSessionStatus.connecting,
                        PrivateSessionStatus.active,
                        PrivateSessionStatus.reconnecting,
                        PrivateSessionStatus.ending,
                    ]
                ),
            )
        )
    ).all()
    for session in sessions:
        try:
            ended_session = await end_private_session(db, None, session.id, reason)
        except StreamingProviderError:
            await _record_livekit_control_failure(
                db,
                operation="delete_room",
                target_type="private_session",
                target_id=session.id,
            )
        else:
            if ended_session.status is not PrivateSessionStatus.ending:
                affected += 1
                await record_event(
                    db,
                    "private_session.authority_terminated",
                    target_type="private_session",
                    target_id=str(session.id),
                    metadata={"reason": reason},
                )
    return affected


async def reconcile_live_compliance_authority(
    db: AsyncSession,
    *,
    limit: int = 100,
    commit_each: bool = True,
) -> int:
    """Evict clients after authority changes without starving later rows.

    ``limit`` is a keyset page size, not a total-run cap. Each target is locked
    and committed independently. Signed join callbacks take the same
    room/session lock first, so a cached token cannot race between provider
    enforcement and the durable terminal state.
    """

    affected = 0
    page_size = max(1, limit)

    async def release_row() -> None:
        if commit_each:
            await db.commit()

    last_room_id: UUID | None = None
    enforceable_room_statuses = [LiveRoomStatus.live, LiveRoomStatus.ending]
    while True:
        query = select(LiveRoom.id).where(LiveRoom.status.in_(enforceable_room_statuses))
        if last_room_id is not None:
            query = query.where(LiveRoom.id > last_room_id)
        room_ids = (await db.scalars(query.order_by(LiveRoom.id).limit(page_size))).all()
        if not room_ids:
            break
        last_room_id = room_ids[-1]
        for room_id in room_ids:
            room = await db.scalar(
                select(LiveRoom)
                .where(
                    LiveRoom.id == room_id,
                    LiveRoom.status.in_(enforceable_room_statuses),
                )
                .with_for_update()
            )
            if room is None:
                await release_row()
                continue
            if room.status is LiveRoomStatus.ending:
                affected += int(
                    await _end_public_room_for_authority(
                        db,
                        room,
                        reason="pending_provider_termination",
                    )
                )
                await release_row()
                continue
            creator = await db.get(CreatorProfile, room.creator_id)
            if creator is None:
                affected += int(
                    await _end_public_room_for_authority(
                        db,
                        room,
                        reason="creator_unavailable",
                    )
                )
                await release_row()
                continue
            creator_user = await db.get(User, creator.user_id)
            creator_eligibility = await resolve_creator_compliance_eligibility(
                db,
                profile=creator,
            )
            try:
                creator_decision = (
                    await require_live_compliance(db, creator_user) if creator_user else None
                )
            except PermissionError:
                creator_decision = None
            if (
                creator.status is not CreatorStatus.approved
                or not creator.is_public
                or not creator_eligibility.public_allowed
                or creator_decision is None
                or not creator_decision.allowed
            ):
                affected += int(
                    await _end_public_room_for_authority(
                        db,
                        room,
                        reason="creator_authority_unavailable",
                    )
                )
                await release_row()
                continue
            participants = (
                await db.scalars(
                    select(LiveParticipant)
                    .where(
                        LiveParticipant.live_room_id == room.id,
                        LiveParticipant.left_at.is_(None),
                        LiveParticipant.user_id != creator.user_id,
                    )
                    .with_for_update()
                )
            ).all()
            for participant in participants:
                viewer = await db.get(User, participant.user_id)
                allowed = bool(viewer and await can_join_live(db, viewer, room))
                banned = bool(
                    await db.scalar(
                        select(LiveBan.id).where(
                            LiveBan.live_room_id == room.id,
                            LiveBan.user_id == participant.user_id,
                        )
                    )
                )
                if allowed and not banned:
                    continue
                affected += int(
                    await _evict_public_participant_for_authority(
                        db,
                        room,
                        participant,
                        reason="viewer_authority_unavailable",
                    )
                )
            await release_row()

    active_private_statuses = [
        PrivateSessionStatus.ready,
        PrivateSessionStatus.connecting,
        PrivateSessionStatus.active,
        PrivateSessionStatus.reconnecting,
        PrivateSessionStatus.ending,
    ]
    last_session_id: UUID | None = None
    while True:
        query = select(PrivateSession.id).where(PrivateSession.status.in_(active_private_statuses))
        if last_session_id is not None:
            query = query.where(PrivateSession.id > last_session_id)
        session_ids = (await db.scalars(query.order_by(PrivateSession.id).limit(page_size))).all()
        if not session_ids:
            break
        last_session_id = session_ids[-1]
        for session_id in session_ids:
            session = await db.scalar(
                select(PrivateSession)
                .where(
                    PrivateSession.id == session_id,
                    PrivateSession.status.in_(active_private_statuses),
                )
                .with_for_update()
            )
            if session is None or (
                session.status is not PrivateSessionStatus.ending
                and await _private_session_authority_allowed(db, session)
            ):
                await release_row()
                continue
            terminal_reason = session.end_reason or "compliance_authority_unavailable"
            if await _enqueue_private_session_termination(db, session, reason=terminal_reason):
                affected += 1
            await release_row()
    return affected


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
    *,
    compliance_decision: ComplianceDecision | None = None,
) -> PrivateSessionRequest:
    await require_live_compliance(db, requester, compliance_decision)
    try:
        creator = await require_public_creator_access(db, creator_id, requester.id)
    except ValueError as exc:
        raise PermissionError("Private session is unavailable") from exc
    if creator.user_id == requester.id:
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
    if invited_user_id:
        invited = await db.get(User, invited_user_id)
        if invited is None:
            raise StreamingError("The invited viewer is unavailable")
        await require_live_compliance(db, invited)
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


async def decline_private_request(
    db: AsyncSession,
    actor: User,
    request_id: UUID,
) -> PrivateSessionRequest:
    """Decline one still-pending request without initiating a payment attempt.

    A request is creator-owned operational state, so this command serializes on
    both the creator profile and request row.  It deliberately does not delete
    the request: the durable status and audit event are needed for support and
    abuse review, while a later creator action can never turn it back into a
    payable session.
    """

    creator = await db.scalar(
        select(CreatorProfile)
        .where(CreatorProfile.user_id == actor.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if creator is None:
        raise PermissionError("Private session request not found")
    request = await db.scalar(
        select(PrivateSessionRequest)
        .where(PrivateSessionRequest.id == request_id)
        .with_for_update()
    )
    if (
        request is None
        or request.creator_id != creator.id
        or request.status is not PrivateRequestStatus.pending
        or request.expires_at <= datetime.now(UTC)
    ):
        raise StreamingError("Private session request is not pending")

    request.status = PrivateRequestStatus.rejected
    await record_event(
        db,
        "private_session.declined",
        actor_user_id=actor.id,
        target_type="private_session_request",
        target_id=str(request.id),
    )
    await emit_transactional(
        db,
        recipient_user_id=request.requester_user_id,
        notification_type="PRIVATE_LIVE_DECLINED",
        source_domain="streaming",
        source_id=str(request.id),
        title="Private session unavailable",
        body="The creator is unavailable for this private session request.",
        target_path="/live",
    )
    return request


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


async def accept_private_request(
    db: AsyncSession,
    actor: User,
    request_id: UUID,
    *,
    compliance_decision: ComplianceDecision | None = None,
) -> PrivateSession:
    # The creator-side decision is independent from the payer's verification
    # and must pass before any request/session/payment state is mutated.
    await lock_compliance_subject(db, actor.id)
    await require_live_compliance(db, actor, compliance_decision)
    creator = await db.scalar(
        select(CreatorProfile)
        .where(CreatorProfile.user_id == actor.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if creator is None or creator.status is not CreatorStatus.approved or not creator.is_public:
        raise PermissionError("Private session request not found")
    creator_eligibility = await resolve_creator_compliance_eligibility(db, profile=creator)
    if not creator_eligibility.public_allowed:
        raise PermissionError("Private session request not found")
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
                    PrivateSessionStatus.ending,
                ]
            ),
        )
        .with_for_update()
    )
    if active:
        raise StreamingError("Creator already has an active private session")
    payer = await db.get(User, request.requester_user_id)
    if not payer:
        raise PermissionError("Private session requester is unavailable")
    await lock_compliance_subject(db, payer.id)
    await require_live_compliance(db, payer)
    await require_private_purchase_compliance(db, payer)
    try:
        await require_public_creator_access(db, creator.id, payer.id)
    except ValueError as exc:
        raise PermissionError("Private session request not found") from exc
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
        provider_reference=new_provider_reference(),
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
    await emit_transactional(
        db,
        recipient_user_id=creator.user_id,
        notification_type="PRIVATE_LIVE_BOOKING",
        source_domain="streaming",
        source_id=str(session.id),
        title="Private session booked",
        body="A private session is awaiting payment authorization.",
        target_path="/live",
    )
    await emit_transactional(
        db,
        recipient_user_id=session.payer_user_id,
        notification_type="PRIVATE_LIVE_STARTED",
        source_domain="streaming",
        source_id=str(session.id),
        title="Private session ready",
        body="Your private session is ready to join.",
        target_path="/live",
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
        select(PrivateSession)
        .where(PrivateSession.id == session_id)
        .with_for_update()
        # A signed callback can have been queued before a concurrent explicit
        # end settles the session.  Refresh after obtaining the row lock so a
        # stale identity-map instance cannot reopen a terminal session.
        .execution_options(populate_existing=True)
    )
    if session is None:
        raise PermissionError("Private session is unavailable")
    # A valid signed join can arrive after explicit end/settlement. Keep its
    # persisted provider-event identity for replay safety, but never allow it
    # to revive a terminal private-session lifecycle.
    if session.status in {
        PrivateSessionStatus.ending,
        PrivateSessionStatus.ended,
        PrivateSessionStatus.settled,
        PrivateSessionStatus.cancelled,
        PrivateSessionStatus.failed,
        PrivateSessionStatus.disputed,
    }:
        return session
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
    last_transition = max(
        (value for value in (participant.joined_at, participant.left_at) if value),
        default=None,
    )
    if last_transition is not None and now < last_transition:
        return session
    if participant.joined_at is not None and participant.left_at is None:
        return session
    if session.status not in (
        PrivateSessionStatus.ready,
        PrivateSessionStatus.connecting,
        PrivateSessionStatus.reconnecting,
    ):
        raise PermissionError("Private session is unavailable")
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
        select(PrivateSession)
        .where(PrivateSession.id == session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if session is None:
        raise PermissionError("Private session is unavailable")
    # Delivery of a provider leave event is asynchronous.  It remains useful
    # to persist/deduplicate that event, but it must never move an already
    # ended, settled, cancelled, failed, or disputed session back to
    # reconnecting (or add billable time).
    if session.status in {
        PrivateSessionStatus.ending,
        PrivateSessionStatus.ended,
        PrivateSessionStatus.settled,
        PrivateSessionStatus.cancelled,
        PrivateSessionStatus.failed,
        PrivateSessionStatus.disputed,
    }:
        return session
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
    last_transition = max(
        (value for value in (participant.joined_at, participant.left_at) if value),
        default=None,
    )
    if last_transition is not None and now < last_transition:
        return session
    if participant.left_at:
        return session
    # LiveKit emits participant_connection_aborted before media becomes ACTIVE.
    # Persist/dedupe the signed event, but an actor who never joined has no
    # billable interval and must not move READY/CONNECTING into reconnecting.
    if participant.joined_at is None:
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
    db: AsyncSession,
    actor: User,
    session_id: UUID,
    *,
    compliance_decision: ComplianceDecision | None = None,
) -> tuple[PrivateSession, str]:
    """Issue a short-lived token only to a named, authorized participant."""
    session_probe = await db.get(PrivateSession, session_id)
    if session_probe is None:
        raise PermissionError("Private session is unavailable")
    required_user_ids = (
        await db.scalars(
            select(SessionParticipant.user_id).where(
                SessionParticipant.private_session_id == session_id
            )
        )
    ).all()
    if actor.id not in required_user_ids:
        raise PermissionError("You are not invited to this private session")
    await lock_compliance_subjects(db, required_user_ids)
    decision = await require_live_compliance(db, actor, compliance_decision)
    session = await db.scalar(
        select(PrivateSession)
        .where(PrivateSession.id == session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
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
    all_participants = (
        await db.scalars(
            select(SessionParticipant).where(SessionParticipant.private_session_id == session.id)
        )
    ).all()
    all_participant_ids = {item.user_id for item in all_participants}
    if await db.scalar(
        select(UserBlock.id).where(
            UserBlock.blocker_user_id.in_(all_participant_ids),
            UserBlock.blocked_user_id.in_(all_participant_ids),
        )
    ):
        raise PermissionError("Private session is unavailable")
    participant_expiries: list[datetime | None] = []
    for required_participant in all_participants:
        required_user = await db.get(User, required_participant.user_id)
        if required_user is None:
            raise PermissionError("Private session is unavailable")
        required_decision = (
            decision
            if required_user.id == actor.id
            else await require_live_compliance(db, required_user)
        )
        participant_expiries.append(required_decision.verification_expires_at)
    creator = await db.get(CreatorProfile, session.creator_id)
    if creator is None or creator.status is not CreatorStatus.approved or not creator.is_public:
        raise PermissionError("Private session is unavailable")
    creator_eligibility = await resolve_creator_compliance_eligibility(db, profile=creator)
    if not creator_eligibility.public_allowed:
        raise PermissionError("Private session is unavailable")
    token = await LiveKitStreamingProvider().participant_token(
        session.provider_room_name,
        str(actor.id),
        can_publish=True,
        can_subscribe=True,
        authority_expires_at=_authority_expiry(
            *participant_expiries,
            (
                creator_eligibility.verification_expires_at
                if creator_eligibility.identity_required or creator_eligibility.age_required
                else None
            ),
        ),
    )
    return session, token


async def block_private_session_for_dispute(
    db: AsyncSession,
    session: PrivateSession,
    *,
    reason: str,
) -> PrivateSession:
    """Deny private delivery and enqueue a durable close before dispute settlement."""

    if session.status not in {
        PrivateSessionStatus.cancelled,
        PrivateSessionStatus.failed,
        PrivateSessionStatus.disputed,
    }:
        await _enqueue_private_session_termination(db, session, reason=reason)
    return session


async def _enqueue_private_session_termination(
    db: AsyncSession,
    session: PrivateSession,
    *,
    reason: str,
    actor_user_id: UUID | None = None,
    now: datetime | None = None,
) -> bool:
    """Persist a private-room ending intent before any LiveKit control call."""

    current = now or datetime.now(UTC)
    pending_intent = session.status is PrivateSessionStatus.ending
    if pending_intent:
        intent_at = session.ended_at or current
        intent_reason = session.end_reason or reason
        intent_actor_id = session.ended_by_user_id
    else:
        intent_at, intent_reason, intent_actor_id = current, reason, actor_user_id
        if session.status is PrivateSessionStatus.active and session.active_started_at:
            session.billable_seconds += max(
                0,
                int((intent_at - session.active_started_at).total_seconds()),
            )
        session.status = PrivateSessionStatus.ending
        session.ended_at = intent_at
        session.end_reason = intent_reason
        session.ended_by_user_id = intent_actor_id
    intent, created = await enqueue_live_provider_control_intent(
        db,
        action=LiveProviderControlAction.delete_room,
        target_type="private_session",
        target_id=str(session.id),
        provider_room_name=session.provider_room_name,
        reason=intent_reason,
        actor_user_id=intent_actor_id,
        idempotency_key=f"private-session-close:{session.id}",
    )
    if created:
        await record_event(
            db,
            "private_session.termination_enqueued",
            actor_user_id=intent_actor_id,
            target_type="private_session",
            target_id=str(session.id),
            metadata={"reason": intent_reason, "intent_id": str(intent.id)},
        )
    return created


async def end_private_session(
    db: AsyncSession,
    actor: User | None,
    session_id: UUID,
    reason: str,
    now: datetime | None = None,
    *,
    provider_room_closed: bool = False,
    propagate_provider_failure: bool = False,
) -> PrivateSession:
    now = now or datetime.now(UTC)
    session = await db.scalar(
        select(PrivateSession).where(PrivateSession.id == session_id).with_for_update()
    )
    if session is None:
        raise PermissionError("Private session is unavailable")
    if actor:
        creator = await db.get(CreatorProfile, session.creator_id)
        if creator is None or actor.id not in {
            session.payer_user_id,
            creator.user_id,
        }:
            raise PermissionError("Only the creator or payer can end this private session")
    terminal_statuses = {
        PrivateSessionStatus.settled,
        PrivateSessionStatus.cancelled,
        PrivateSessionStatus.failed,
        PrivateSessionStatus.disputed,
    }
    if session.status in terminal_statuses:
        return session
    if reason != "provider_room_finished" and not provider_room_closed:
        await _enqueue_private_session_termination(
            db,
            session,
            reason=reason,
            actor_user_id=actor.id if actor else None,
            now=now,
        )
        return session
    pending_intent = session.status is PrivateSessionStatus.ending
    intent_at = session.ended_at if pending_intent and session.ended_at else now
    intent_reason = session.end_reason if pending_intent and session.end_reason else reason
    intent_actor_id = session.ended_by_user_id if pending_intent else actor.id if actor else None
    # A signed room-finished event is already authoritative provider control,
    # so it bypasses the outbox but must still close the final active billing
    # interval exactly once.
    if (
        not pending_intent
        and session.status is PrivateSessionStatus.active
        and session.active_started_at is not None
    ):
        session.billable_seconds += max(
            0,
            int((intent_at - session.active_started_at).total_seconds()),
        )
    # No required participants reached ACTIVE, so no service was delivered and
    # the configured minimum must not be charged.
    if session.billable_seconds == 0 and session.active_started_at is None:
        session.status, session.ended_at, session.end_reason = (
            PrivateSessionStatus.cancelled,
            intent_at,
            intent_reason,
        )
        session.ended_by_user_id = intent_actor_id
        return session
    session.status, session.ended_at, session.end_reason = (
        PrivateSessionStatus.ended,
        intent_at,
        intent_reason,
    )
    session.ended_by_user_id = intent_actor_id
    return await settle_private_session(db, session)


async def expire_reconnect_grace(
    db: AsyncSession,
    now: datetime | None = None,
    *,
    limit: int = 100,
) -> int:
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(seconds=get_settings().streaming_reconnect_grace_seconds)
    processed = 0
    # Process and commit one row at a time. A LiveKit outage must not hold an
    # entire expiry cohort locked across serial provider timeouts, and SKIP
    # LOCKED prevents overlapping workers from queueing behind the same row.
    for _ in range(max(1, limit)):
        session = await db.scalar(
            select(PrivateSession)
            .where(
                PrivateSession.status == PrivateSessionStatus.reconnecting,
                PrivateSession.disconnected_at <= cutoff,
            )
            .order_by(PrivateSession.disconnected_at, PrivateSession.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if session is None:
            break
        await end_private_session(db, None, session.id, "reconnect_grace_expired", now)
        processed += 1
        await db.commit()
    return processed


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


async def reconcile_private_provider_presence(
    db: AsyncSession,
    limit: int = 100,
    *,
    now: datetime | None = None,
) -> int:
    """Repair delayed LiveKit lifecycle callbacks from authoritative room membership.

    The provider list is intentionally the only repair authority here.  It can
    confirm a join that reached LiveKit when the signed callback was delayed,
    and it can freeze billing for a previously connected participant that is
    no longer present.  Browser UI state is never used for either transition.
    """
    repaired = 0
    provider = LiveKitStreamingProvider()
    checked_at = now or datetime.now(UTC)
    active_statuses = [
        PrivateSessionStatus.ready,
        PrivateSessionStatus.connecting,
        PrivateSessionStatus.active,
        PrivateSessionStatus.reconnecting,
    ]
    # ``last_heartbeat_at`` is the durable provider-presence check cursor. Each
    # bounded run advances checked rows to the back of the queue, so later
    # sessions are never starved and overlapping 10-second tasks stay bounded.
    session_ids = (
        await db.scalars(
            select(PrivateSession.id)
            .where(PrivateSession.status.in_(active_statuses))
            .order_by(
                PrivateSession.last_heartbeat_at.asc().nulls_first(),
                PrivateSession.id,
            )
            .limit(max(1, limit))
        )
    ).all()
    for session_id in session_ids:
        session = await db.scalar(
            select(PrivateSession)
            .where(
                PrivateSession.id == session_id,
                PrivateSession.status.in_(active_statuses),
            )
            .with_for_update(skip_locked=True)
        )
        if session is None:
            await db.commit()
            continue
        try:
            identities = await provider.list_participant_identities(session.provider_room_name)
        except StreamingProviderError:
            session.last_heartbeat_at = checked_at
            await db.commit()
            continue
        participants = (
            await db.scalars(
                select(SessionParticipant)
                .where(SessionParticipant.private_session_id == session.id)
                .with_for_update()
            )
        ).all()
        for participant in participants:
            actor = await db.get(User, participant.user_id)
            if actor is None:
                continue
            if str(participant.user_id) in identities:
                if participant.left_at is not None or participant.joined_at is None:
                    await private_participant_connected(db, actor, session.id, checked_at)
                    repaired += 1
            # A participant who has never joined is not a disconnect. In
            # particular, do not move an authorized READY session into the
            # reconnecting state merely because its room is still empty.
            elif participant.joined_at is not None and participant.left_at is None:
                await private_participant_disconnected(db, actor, session.id, checked_at)
                repaired += 1
        session.last_heartbeat_at = checked_at
        await db.commit()
    return repaired


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
        # Keep receipt/processing time distinct from the signed provider
        # occurrence used for lifecycle and billing calculations.
        processed_at=datetime.now(UTC),
    )
    db.add(event)
    # The uniqueness record is deliberately flushed before participant state or
    # billable time changes, so a retried provider callback is harmless.
    await db.flush()
    actor = await db.get(User, user_id)
    if actor is None:
        raise PermissionError("Provider participant is unknown")
    if event_type == "participant_joined":
        return await private_participant_connected(db, actor, session_id, now)
    if event_type == "participant_left":
        return await private_participant_disconnected(db, actor, session_id, now)
    if event_type == "participant_connection_aborted":
        return await private_participant_disconnected(db, actor, session_id, now)
    raise StreamingError("Unsupported provider event")


async def _process_public_provider_participant_event(
    db: AsyncSession,
    *,
    room: LiveRoom,
    event_id: str,
    event_type: str,
    participant_identity: str | None,
) -> None:
    if event_type not in {
        "participant_joined",
        "participant_left",
        "participant_connection_aborted",
    }:
        return
    if not participant_identity:
        raise StreamingError("LiveKit participant event is incomplete")
    try:
        user_id = UUID(participant_identity)
    except ValueError as exc:
        raise StreamingError("LiveKit participant identity is invalid") from exc
    participant = await db.scalar(
        select(LiveParticipant)
        .where(
            LiveParticipant.live_room_id == room.id,
            LiveParticipant.user_id == user_id,
        )
        .with_for_update()
    )
    if event_type != "participant_joined":
        if participant is not None and participant.left_at is None:
            participant.left_at = datetime.now(UTC)
            if participant.role is LiveParticipantRole.viewer:
                room.viewer_count = max(0, room.viewer_count - 1)
        return

    # DeleteRoom does not invalidate a cached self-hosted LiveKit token. If a
    # client recreates an ended room, delete it again on the signed join event.
    if room.status is not LiveRoomStatus.live:
        await _enqueue_public_room_termination(
            db,
            room,
            reason="cached_token_rejoin_after_termination",
            idempotency_suffix=event_id,
        )
        return

    user = await db.get(User, user_id)
    banned = bool(
        await db.scalar(
            select(LiveBan.id).where(
                LiveBan.live_room_id == room.id,
                LiveBan.user_id == user_id,
            )
        )
    )
    allowed = bool(
        participant is not None
        and user is not None
        and not banned
        and await can_join_live(db, user, room)
    )
    if not allowed:
        # A previously evicted participant can reconnect with a cached
        # self-hosted token. Use the signed provider-event id as the durable
        # command generation; a completed earlier eviction must never suppress
        # a later provider-side rejoin removal.
        await enqueue_live_provider_control_intent(
            db,
            action=LiveProviderControlAction.remove_participant,
            target_type="live_room_identity",
            target_id=str(room.id),
            provider_room_name=room.provider_room_name,
            participant_identity=participant_identity,
            reason="cached_token_join_denied",
            idempotency_key=f"live-room-identity-remove:{room.id}:{event_id}",
        )
        return
    if participant.left_at is not None:
        participant.left_at = None
        participant.joined_at = datetime.now(UTC)
        if participant.role is LiveParticipantRole.viewer:
            room.viewer_count += 1
            room.peak_viewer_count = max(room.peak_viewer_count, room.viewer_count)


def _private_provider_event_time(
    event: dict,
    *,
    session: PrivateSession,
    received_at: datetime,
) -> datetime:
    """Normalize LiveKit's signed protobuf ``createdAt`` Unix-second string."""

    raw = event.get("createdAt")
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        raise StreamingError("LiveKit participant event timestamp is invalid")
    if isinstance(raw, str) and (not raw.isascii() or not raw.isdecimal()):
        raise StreamingError("LiveKit participant event timestamp is invalid")
    try:
        seconds = int(raw)
        if seconds <= 0:
            raise ValueError
        occurred_at = datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise StreamingError("LiveKit participant event timestamp is invalid") from exc
    floor = session.ready_at or session.accepted_at
    if floor.tzinfo is None:
        floor = floor.replace(tzinfo=UTC)
    # Never bill before authorization or into the future because of provider
    # clock skew. Signed occurrence remains authoritative inside those bounds.
    return max(floor, min(occurred_at, received_at))


async def process_livekit_webhook(db: AsyncSession, event: dict) -> PrivateSession | None:
    """Map a verified LiveKit event into persisted domain state without browser input."""
    room_name = (event.get("room") or {}).get("name")
    participant_identity = (event.get("participant") or {}).get("identity")
    event_type = event.get("event")
    if not room_name or not event_type:
        raise StreamingError("LiveKit event is incomplete")
    participant_user_id: UUID | None = None
    if event_type in {
        "participant_joined",
        "participant_left",
        "participant_connection_aborted",
    }:
        if not participant_identity:
            raise StreamingError("LiveKit participant event is incomplete")
        try:
            participant_user_id = UUID(participant_identity)
        except ValueError as exc:
            raise StreamingError("LiveKit participant identity is invalid") from exc
        # Signed cached-token joins are provider-capability mutations too.
        # Acquire the authority lock before any room/session row lock so a
        # concurrent revocation cannot miss or race the rejoin.
        await lock_compliance_subject(db, participant_user_id)
    session = await db.scalar(
        select(PrivateSession)
        .where(PrivateSession.provider_room_name == room_name)
        .with_for_update()
    )
    if session is None:
        # Public room lifecycle has no billable participant transitions. Record
        # it for audit/replay safety without allowing a provider event to reopen it.
        room = await db.scalar(
            select(LiveRoom).where(LiveRoom.provider_room_name == room_name).with_for_update()
        )
        if room is None:
            raise PermissionError("LiveKit room is unknown")
        existing = await db.scalar(
            select(ProviderLiveEvent).where(
                ProviderLiveEvent.provider == "livekit",
                ProviderLiveEvent.external_event_id == event["id"],
            )
        )
        if existing:
            return None
        db.add(
            ProviderLiveEvent(
                provider="livekit",
                external_event_id=event["id"],
                event_type=event_type,
                live_room_id=room.id,
                processed_at=datetime.now(UTC),
            )
        )
        await db.flush()
        if event_type == "room_finished" and room.status in {
            LiveRoomStatus.live,
            LiveRoomStatus.ending,
        }:
            await _mark_public_room_ended(db, room)
        await _process_public_provider_participant_event(
            db,
            room=room,
            event_id=event["id"],
            event_type=event_type,
            participant_identity=participant_identity,
        )
        return None
    if event_type == "room_finished":
        received_at = datetime.now(UTC)
        occurred_at = _private_provider_event_time(
            event,
            session=session,
            received_at=received_at,
        )
        existing = await db.scalar(
            select(ProviderLiveEvent).where(
                ProviderLiveEvent.provider == "livekit",
                ProviderLiveEvent.external_event_id == event["id"],
            )
        )
        if existing:
            return None
        db.add(
            ProviderLiveEvent(
                provider="livekit",
                external_event_id=event["id"],
                event_type=event_type,
                private_session_id=session.id,
                processed_at=received_at,
            )
        )
        await db.flush()
        return await end_private_session(
            db,
            None,
            session.id,
            "provider_room_finished",
            occurred_at,
        )
    if event_type not in {
        "participant_joined",
        "participant_left",
        "participant_connection_aborted",
    }:
        return None
    assert participant_user_id is not None
    user_id = participant_user_id
    occurred_at = _private_provider_event_time(
        event,
        session=session,
        received_at=datetime.now(UTC),
    )
    if event_type == "participant_joined" and session.status in {
        PrivateSessionStatus.ending,
        PrivateSessionStatus.ended,
        PrivateSessionStatus.settled,
        PrivateSessionStatus.cancelled,
        PrivateSessionStatus.failed,
        PrivateSessionStatus.disputed,
    }:
        await enqueue_live_provider_control_intent(
            db,
            action=LiveProviderControlAction.delete_room,
            target_type="private_session",
            target_id=str(session.id),
            provider_room_name=session.provider_room_name,
            reason=session.end_reason or "cached_token_rejoin_after_termination",
            actor_user_id=session.ended_by_user_id,
            idempotency_key=f"private-session-reclose:{session.id}:{event['id']}",
        )
    elif event_type == "participant_joined":
        participant = await db.scalar(
            select(SessionParticipant.id).where(
                SessionParticipant.private_session_id == session.id,
                SessionParticipant.user_id == user_id,
            )
        )
        if participant is None or not await _private_session_authority_allowed(
            db,
            session,
        ):
            # A cached token must not reopen private delivery between an
            # authority change and the periodic sweep. Delete first; if the
            # provider call fails, propagate so the signed event is not
            # acknowledged/deduplicated and LiveKit can retry it.
            await end_private_session(
                db,
                None,
                session.id,
                "compliance_authority_unavailable",
                propagate_provider_failure=True,
            )
    return await process_private_provider_event(
        db,
        event_id=event["id"],
        event_type=event_type,
        session_id=session.id,
        user_id=user_id,
        now=occurred_at,
    )


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
    event_at = session.ended_at or session.active_started_at or session.created_at
    allocation_entries, allocation_metadata = await finance.creator_revenue_allocation(
        db,
        session.creator_id,
        session.currency,
        creator_amount,
        event_at,
    )
    from app.referrals.service import record_revenue_allocation, revenue_allocation

    referral_entries, referral_allocation = await revenue_allocation(
        db,
        buyer_user_id=session.payer_user_id,
        revenue_type="private_live",
        currency=session.currency,
        platform_fee_minor=fee,
        occurred_at=event_at,
    )
    referral_amount = int(referral_allocation["amount_minor"]) if referral_allocation else 0
    ledger = await finance.post_entries(
        db,
        transaction_type=LedgerTransactionType.private_live_session,
        currency=session.currency,
        idempotency_key=f"private_session:{session.id}",
        reference=f"private_session:{session.id}",
        entries=[
            (clearing, LedgerDirection.debit, gross),
            (revenue, LedgerDirection.credit, fee - referral_amount),
            *referral_entries,
            *allocation_entries,
        ],
        metadata={
            "private_session_id": str(session.id),
            "billable_seconds": str(session.billable_seconds),
            "platform_fee_minor": str(fee),
            "referral_amount_minor": str(referral_amount),
            **allocation_metadata,
        },
    )
    await record_revenue_allocation(
        db,
        source_ledger_transaction_id=ledger.id,
        allocation=referral_allocation,
    )
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


_PRIVATE_REVERSAL_TYPES = {
    LedgerTransactionType.refund,
    LedgerTransactionType.chargeback,
}


async def _validate_private_settlement_ledger(
    db: AsyncSession, original: LedgerTransaction, settlement: PrivateSessionSettlement
) -> None:
    """Verify that the settlement snapshot still identifies one balanced exact charge."""
    original_entries = (
        await db.scalars(select(LedgerEntry).where(LedgerEntry.transaction_id == original.id))
    ).all()
    debits = sum(
        entry.amount_minor for entry in original_entries if entry.direction is LedgerDirection.debit
    )
    credits = sum(
        entry.amount_minor
        for entry in original_entries
        if entry.direction is LedgerDirection.credit
    )
    if debits != settlement.gross_amount_minor or credits != settlement.gross_amount_minor:
        raise StreamingError("Private-session settlement ledger snapshot is inconsistent")


async def reverse_private_session_payment(
    db: AsyncSession,
    payment_attempt: PaymentAttempt,
    *,
    resolution_type: LedgerTransactionType,
    reason: str,
) -> PrivateSession | None:
    """Apply one signed provider refund/chargeback to its private session.

    The caller owns signature verification and provider-event deduplication. This
    domain hook locks the attempt and session, reverses the exact frozen
    settlement at most once, and makes private-room access terminal. A
    chargeback dominates a refund when provider events arrive out of order.
    """
    if resolution_type not in _PRIVATE_REVERSAL_TYPES:
        raise StreamingError("Private-session payment reversal type is invalid")
    attempt = await db.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == payment_attempt.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if attempt is None:
        return None
    session = await db.scalar(
        select(PrivateSession)
        .where(PrivateSession.payment_attempt_id == attempt.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if session is None:
        return None

    if session.status in {
        PrivateSessionStatus.awaiting_payment_authorization,
        PrivateSessionStatus.ready,
        PrivateSessionStatus.connecting,
        PrivateSessionStatus.active,
        PrivateSessionStatus.reconnecting,
        PrivateSessionStatus.ending,
        PrivateSessionStatus.ended,
    }:
        # A refund/chargeback denies future API/token access immediately and
        # commits a room-close command with the financial transition. LiveKit
        # is invoked only by the post-commit outbox processor.
        await _enqueue_private_session_termination(db, session, reason=reason)

    settlement = await db.scalar(
        select(PrivateSessionSettlement)
        .where(PrivateSessionSettlement.private_session_id == session.id)
        .with_for_update()
    )
    reversal: LedgerTransaction | None = None
    if settlement is not None:
        original = await db.get(LedgerTransaction, settlement.ledger_transaction_id)
        if (
            original is None
            or original.transaction_type is not LedgerTransactionType.private_live_session
            or original.currency != settlement.currency
            or settlement.currency != session.currency
        ):
            raise StreamingError("Private-session settlement snapshot is inconsistent")
        await _validate_private_settlement_ledger(db, original, settlement)
        reversal = await db.scalar(
            select(LedgerTransaction)
            .where(
                LedgerTransaction.reversal_of_transaction_id == original.id,
                LedgerTransaction.transaction_type.in_(_PRIVATE_REVERSAL_TYPES),
            )
            .order_by(LedgerTransaction.created_at, LedgerTransaction.id)
            .with_for_update()
        )
        if reversal is None:
            reversal = await finance.reverse_original_ledger(
                db,
                original.id,
                transaction_type=resolution_type,
                idempotency_key=f"private-session-reversal:{session.id}",
                reference=f"private_session_reversal:{session.id}",
                metadata={
                    "private_session_id": str(session.id),
                    "private_session_settlement_id": str(settlement.id),
                    "original_ledger_transaction_id": str(original.id),
                    "reason": reason,
                    "resolution_type": resolution_type.value,
                },
            )
        elif reversal.currency != settlement.currency:
            raise StreamingError("Private-session reversal ledger snapshot is inconsistent")
        await _validate_private_settlement_ledger(db, reversal, settlement)
    elif session.status is PrivateSessionStatus.settled:
        raise StreamingError("Private-session settlement snapshot is missing")

    previous_session_status = session.status
    previous_attempt_status = attempt.status
    chargeback_dominates = (
        resolution_type is LedgerTransactionType.chargeback
        or attempt.status is PaymentStatus.chargeback
        or (reversal is not None and reversal.transaction_type is LedgerTransactionType.chargeback)
    )
    session.status = (
        PrivateSessionStatus.disputed if chargeback_dominates else PrivateSessionStatus.cancelled
    )
    attempt.status = PaymentStatus.chargeback if chargeback_dominates else PaymentStatus.refunded
    now = datetime.now(UTC)
    if session.ended_at is None:
        session.ended_at = now
    if session.end_reason is None:
        session.end_reason = "provider_chargeback" if chargeback_dominates else "provider_refund"

    if (
        session.status is not previous_session_status
        or attempt.status is not previous_attempt_status
    ):
        await record_event(
            db,
            "private_session.payment_reversed",
            actor_user_id=session.payer_user_id,
            target_type="private_session",
            target_id=str(session.id),
            metadata={
                "resolution_type": (
                    LedgerTransactionType.chargeback.value
                    if chargeback_dominates
                    else LedgerTransactionType.refund.value
                ),
                "reason": reason,
                "payment_attempt_id": str(attempt.id),
                "reversal_ledger_transaction_id": str(reversal.id) if reversal else "",
            },
        )
    return session
