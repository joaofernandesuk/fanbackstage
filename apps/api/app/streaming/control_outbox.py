"""Durable LiveKit control commands with committed-transaction visibility and replay."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.service import record_event
from app.db.session import SessionLocal
from app.integrations.streaming import StreamingProvider
from app.models.streaming import (
    LiveProviderControlAction,
    LiveProviderControlIntent,
    LiveProviderControlStatus,
)

LIVE_PROVIDER_CONTROL_LEASE_SECONDS = 60
LIVE_PROVIDER_CONTROL_RETRY_BASE_SECONDS = 5
LIVE_PROVIDER_CONTROL_RETRY_MAX_SECONDS = 300
LIVE_PROVIDER_CONTROL_BATCH_LIMIT = 50
LIVE_PROVIDER_CONTROL_MAX_BATCH_LIMIT = 500

ProviderFactory = Callable[[], StreamingProvider]
SessionFactory = async_sessionmaker[AsyncSession]
LiveProviderControlSuccessHook = Callable[
    [AsyncSession, LiveProviderControlIntent], Awaitable[None]
]


class LiveProviderControlError(ValueError):
    pass


class LiveProviderControlStructuralError(LiveProviderControlError):
    """Safe terminal signal for a persisted command that cannot match its domain target."""

    def __init__(self, error_code: str):
        super().__init__(error_code)
        self.error_code = error_code[:96]


@dataclass(frozen=True)
class LiveProviderControlIntentSnapshot:
    id: UUID
    created_at: datetime
    updated_at: datetime
    action: LiveProviderControlAction
    target_type: str
    target_id: str
    provider_room_name: str
    participant_identity: str | None
    reason: str
    actor_user_id: UUID | None
    idempotency_key: str
    status: LiveProviderControlStatus
    retryable: bool
    attempt_count: int
    last_attempt_at: datetime | None
    lease_expires_at: datetime | None
    next_attempt_at: datetime | None
    succeeded_at: datetime | None
    terminal_failed_at: datetime | None
    last_error_code: str | None
    last_error_at: datetime | None


@dataclass(frozen=True)
class LiveProviderControlProcessResult:
    intent: LiveProviderControlIntentSnapshot
    provider_invoked: bool

    @property
    def succeeded(self) -> bool:
        return self.intent.status is LiveProviderControlStatus.succeeded


@dataclass(frozen=True)
class LiveProviderControlBatchResult:
    results: tuple[LiveProviderControlProcessResult, ...]

    @property
    def processed_count(self) -> int:
        return len(self.results)

    @property
    def succeeded_count(self) -> int:
        return sum(result.succeeded for result in self.results)

    @property
    def retryable_count(self) -> int:
        return sum(result.intent.retryable for result in self.results)

    @property
    def terminal_count(self) -> int:
        return sum(
            result.intent.status is LiveProviderControlStatus.failed_terminal
            for result in self.results
        )

    @property
    def succeeded_intents(self) -> tuple[LiveProviderControlIntentSnapshot, ...]:
        return tuple(result.intent for result in self.results if result.succeeded)


@dataclass(frozen=True)
class _ClaimedIntent:
    intent: LiveProviderControlIntentSnapshot
    provider_invoked: bool


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current if current.tzinfo is not None else current.replace(tzinfo=UTC)


def _required_text(value: str | None, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise LiveProviderControlError(f"{field} must be text")
    normalized = value.strip()
    if not normalized:
        raise LiveProviderControlError(f"{field} is required")
    if len(normalized) > maximum:
        raise LiveProviderControlError(f"{field} exceeds {maximum} characters")
    return normalized


def _normalize_payload(
    *,
    action: LiveProviderControlAction | str,
    target_type: str,
    target_id: str,
    provider_room_name: str,
    participant_identity: str | None,
    reason: str,
    idempotency_key: str,
) -> tuple[LiveProviderControlAction, str, str, str, str | None, str, str]:
    try:
        normalized_action = LiveProviderControlAction(action)
    except ValueError as exc:
        raise LiveProviderControlError("Unsupported LiveKit provider-control action") from exc
    normalized_target_type = _required_text(target_type, "target_type", 80)
    normalized_target_id = _required_text(target_id, "target_id", 255)
    normalized_room = _required_text(provider_room_name, "provider_room_name", 128)
    normalized_reason = _required_text(reason, "reason", 500)
    normalized_key = _required_text(idempotency_key, "idempotency_key", 160)
    if normalized_action is LiveProviderControlAction.delete_room:
        if participant_identity is not None:
            raise LiveProviderControlError("delete_room cannot include participant_identity")
        normalized_identity = None
    else:
        normalized_identity = _required_text(
            participant_identity,
            "participant_identity",
            255,
        )
    return (
        normalized_action,
        normalized_target_type,
        normalized_target_id,
        normalized_room,
        normalized_identity,
        normalized_reason,
        normalized_key,
    )


def _snapshot(intent: LiveProviderControlIntent) -> LiveProviderControlIntentSnapshot:
    return LiveProviderControlIntentSnapshot(
        id=intent.id,
        created_at=intent.created_at,
        updated_at=intent.updated_at,
        action=intent.action,
        target_type=intent.target_type,
        target_id=intent.target_id,
        provider_room_name=intent.provider_room_name,
        participant_identity=intent.participant_identity,
        reason=intent.reason,
        actor_user_id=intent.actor_user_id,
        idempotency_key=intent.idempotency_key,
        status=intent.status,
        retryable=intent.retryable,
        attempt_count=intent.attempt_count,
        last_attempt_at=intent.last_attempt_at,
        lease_expires_at=intent.lease_expires_at,
        next_attempt_at=intent.next_attempt_at,
        succeeded_at=intent.succeeded_at,
        terminal_failed_at=intent.terminal_failed_at,
        last_error_code=intent.last_error_code,
        last_error_at=intent.last_error_at,
    )


def _payload_matches(
    intent: LiveProviderControlIntent,
    *,
    action: LiveProviderControlAction,
    target_type: str,
    target_id: str,
    provider_room_name: str,
    participant_identity: str | None,
    reason: str,
    actor_user_id: UUID | None,
) -> bool:
    return (
        intent.action is action
        and intent.target_type == target_type
        and intent.target_id == target_id
        and intent.provider_room_name == provider_room_name
        and intent.participant_identity == participant_identity
        and intent.reason == reason
        and intent.actor_user_id == actor_user_id
    )


async def enqueue_live_provider_control_intent(
    db: AsyncSession,
    *,
    action: LiveProviderControlAction | str,
    target_type: str,
    target_id: str,
    provider_room_name: str,
    participant_identity: str | None = None,
    reason: str,
    actor_user_id: UUID | None = None,
    idempotency_key: str,
) -> tuple[LiveProviderControlIntent, bool]:
    """Persist a command in the caller's transaction; never call LiveKit here."""

    (
        normalized_action,
        normalized_target_type,
        normalized_target_id,
        normalized_room,
        normalized_identity,
        normalized_reason,
        normalized_key,
    ) = _normalize_payload(
        action=action,
        target_type=target_type,
        target_id=target_id,
        provider_room_name=provider_room_name,
        participant_identity=participant_identity,
        reason=reason,
        idempotency_key=idempotency_key,
    )
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:idempotency_key, 0))"),
        {"idempotency_key": normalized_key},
    )
    existing = await db.scalar(
        select(LiveProviderControlIntent).where(
            LiveProviderControlIntent.idempotency_key == normalized_key
        )
    )
    if existing is not None:
        if not _payload_matches(
            existing,
            action=normalized_action,
            target_type=normalized_target_type,
            target_id=normalized_target_id,
            provider_room_name=normalized_room,
            participant_identity=normalized_identity,
            reason=normalized_reason,
            actor_user_id=actor_user_id,
        ):
            raise LiveProviderControlError(
                "idempotency_key is already bound to a different provider-control command"
            )
        return existing, False

    intent = LiveProviderControlIntent(
        action=normalized_action,
        target_type=normalized_target_type,
        target_id=normalized_target_id,
        provider_room_name=normalized_room,
        participant_identity=normalized_identity,
        reason=normalized_reason,
        actor_user_id=actor_user_id,
        idempotency_key=normalized_key,
        status=LiveProviderControlStatus.pending,
        retryable=True,
        attempt_count=0,
        next_attempt_at=_now(),
    )
    db.add(intent)
    await db.flush()
    return intent, True


def _is_due(intent: LiveProviderControlIntent, current: datetime) -> bool:
    if intent.status is LiveProviderControlStatus.pending:
        return intent.next_attempt_at is not None and intent.next_attempt_at <= current
    if intent.status is LiveProviderControlStatus.processing:
        return intent.lease_expires_at is not None and intent.lease_expires_at <= current
    return False


async def _claim_next_intent(
    *,
    intent_id: UUID | None,
    session_factory: SessionFactory,
    current: datetime,
) -> _ClaimedIntent | None:
    async with session_factory() as db:
        if intent_id is not None:
            intent = await db.scalar(
                select(LiveProviderControlIntent)
                .where(LiveProviderControlIntent.id == intent_id)
                .with_for_update(skip_locked=True)
            )
            if intent is None:
                return None
            if intent.status in {
                LiveProviderControlStatus.succeeded,
                LiveProviderControlStatus.failed_terminal,
            }:
                return _ClaimedIntent(_snapshot(intent), provider_invoked=False)
            if not _is_due(intent, current):
                return None
        else:
            due = or_(
                and_(
                    LiveProviderControlIntent.status == LiveProviderControlStatus.pending,
                    LiveProviderControlIntent.next_attempt_at <= current,
                ),
                and_(
                    LiveProviderControlIntent.status == LiveProviderControlStatus.processing,
                    LiveProviderControlIntent.lease_expires_at <= current,
                ),
            )
            intent = await db.scalar(
                select(LiveProviderControlIntent)
                .where(due)
                .order_by(
                    func.coalesce(
                        LiveProviderControlIntent.next_attempt_at,
                        LiveProviderControlIntent.lease_expires_at,
                    ),
                    LiveProviderControlIntent.created_at,
                    LiveProviderControlIntent.id,
                )
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if intent is None:
                return None

        intent.status = LiveProviderControlStatus.processing
        intent.retryable = True
        intent.attempt_count += 1
        intent.last_attempt_at = current
        intent.lease_expires_at = current + timedelta(seconds=LIVE_PROVIDER_CONTROL_LEASE_SECONDS)
        intent.next_attempt_at = None
        await db.flush()
        await db.refresh(intent)
        claimed = _snapshot(intent)
        await db.commit()
        return _ClaimedIntent(claimed, provider_invoked=True)


def _structural_error(intent: LiveProviderControlIntentSnapshot) -> str | None:
    required = (
        intent.target_type,
        intent.target_id,
        intent.provider_room_name,
        intent.reason,
        intent.idempotency_key,
    )
    if any(not value.strip() for value in required):
        return "STRUCTURALLY_INVALID_REQUIRED_FIELD"
    if intent.action is LiveProviderControlAction.delete_room:
        return (
            "STRUCTURALLY_INVALID_DELETE_ROOM_IDENTITY"
            if intent.participant_identity is not None
            else None
        )
    if intent.action is LiveProviderControlAction.remove_participant:
        if intent.participant_identity is None or not intent.participant_identity.strip():
            return "STRUCTURALLY_INVALID_PARTICIPANT_IDENTITY"
        return None
    return "STRUCTURALLY_INVALID_ACTION"


def _audit_metadata(intent: LiveProviderControlIntent) -> dict[str, object]:
    return {
        "action": intent.action.value,
        "source_target_type": intent.target_type,
        "source_target_id": intent.target_id,
        "provider_room_name": intent.provider_room_name,
        "reason": intent.reason,
        "attempt_count": intent.attempt_count,
    }


def _retry_delay(attempt_count: int) -> timedelta:
    exponent = min(max(attempt_count - 1, 0), 16)
    seconds = min(
        LIVE_PROVIDER_CONTROL_RETRY_MAX_SECONDS,
        LIVE_PROVIDER_CONTROL_RETRY_BASE_SECONDS * (2**exponent),
    )
    return timedelta(seconds=seconds)


def _provider_error_code(exc: Exception) -> str:
    return f"PROVIDER_{type(exc).__name__.upper()}"[:96]


async def _finalize_successful_intent(
    *,
    intent_id: UUID,
    expected_attempt_count: int,
    success_hook: LiveProviderControlSuccessHook,
    session_factory: SessionFactory,
    current: datetime,
) -> LiveProviderControlIntentSnapshot:
    async with session_factory() as db:
        intent = await db.scalar(
            select(LiveProviderControlIntent)
            .where(LiveProviderControlIntent.id == intent_id)
            .with_for_update()
        )
        if intent is None:
            raise LiveProviderControlError("Provider-control intent no longer exists")
        if intent.status is LiveProviderControlStatus.succeeded:
            return _snapshot(intent)
        if intent.status is LiveProviderControlStatus.failed_terminal:
            return _snapshot(intent)
        if (
            intent.status is not LiveProviderControlStatus.processing
            or intent.attempt_count != expected_attempt_count
        ):
            return _snapshot(intent)
        # Domain state and outbox success share one transaction. The hook must
        # be idempotent because a lost commit acknowledgement can replay it.
        await success_hook(db, intent)
        intent.status = LiveProviderControlStatus.succeeded
        intent.retryable = False
        intent.lease_expires_at = None
        intent.next_attempt_at = None
        intent.succeeded_at = current
        await record_event(
            db,
            "streaming.provider_control_succeeded",
            actor_user_id=intent.actor_user_id,
            target_type="live_provider_control_intent",
            target_id=str(intent.id),
            metadata={**_audit_metadata(intent), "succeeded_at": current.isoformat()},
        )
        await db.flush()
        await db.refresh(intent)
        completed = _snapshot(intent)
        await db.commit()
        return completed


async def _finalize_retryable_failure(
    *,
    intent_id: UUID,
    expected_attempt_count: int,
    error_code: str,
    session_factory: SessionFactory,
    current: datetime,
) -> LiveProviderControlIntentSnapshot:
    async with session_factory() as db:
        intent = await db.scalar(
            select(LiveProviderControlIntent)
            .where(LiveProviderControlIntent.id == intent_id)
            .with_for_update()
        )
        if intent is None:
            raise LiveProviderControlError("Provider-control intent no longer exists")
        if (
            intent.status is not LiveProviderControlStatus.processing
            or intent.attempt_count != expected_attempt_count
        ):
            return _snapshot(intent)
        intent.status = LiveProviderControlStatus.pending
        intent.retryable = True
        intent.lease_expires_at = None
        intent.next_attempt_at = current + _retry_delay(intent.attempt_count)
        intent.last_error_code = error_code
        intent.last_error_at = current
        await record_event(
            db,
            "streaming.provider_control_retry_scheduled",
            actor_user_id=intent.actor_user_id,
            target_type="live_provider_control_intent",
            target_id=str(intent.id),
            metadata={
                **_audit_metadata(intent),
                "error_code": error_code,
                "retryable": True,
                "next_attempt_at": intent.next_attempt_at.isoformat(),
            },
        )
        await db.flush()
        await db.refresh(intent)
        pending = _snapshot(intent)
        await db.commit()
        return pending


async def _finalize_structural_failure(
    *,
    intent_id: UUID,
    expected_attempt_count: int,
    error_code: str,
    session_factory: SessionFactory,
    current: datetime,
) -> LiveProviderControlIntentSnapshot:
    async with session_factory() as db:
        intent = await db.scalar(
            select(LiveProviderControlIntent)
            .where(LiveProviderControlIntent.id == intent_id)
            .with_for_update()
        )
        if intent is None:
            raise LiveProviderControlError("Provider-control intent no longer exists")
        if (
            intent.status is not LiveProviderControlStatus.processing
            or intent.attempt_count != expected_attempt_count
        ):
            return _snapshot(intent)
        intent.status = LiveProviderControlStatus.failed_terminal
        intent.retryable = False
        intent.lease_expires_at = None
        intent.next_attempt_at = None
        intent.terminal_failed_at = current
        intent.last_error_code = error_code
        intent.last_error_at = current
        await record_event(
            db,
            "streaming.provider_control_failed_terminal",
            actor_user_id=intent.actor_user_id,
            target_type="live_provider_control_intent",
            target_id=str(intent.id),
            metadata={
                **_audit_metadata(intent),
                "error_code": error_code,
                "retryable": False,
                "terminal_failed_at": current.isoformat(),
            },
        )
        await db.flush()
        await db.refresh(intent)
        terminal = _snapshot(intent)
        await db.commit()
        return terminal


def _default_provider_factory() -> StreamingProvider:
    # This lazy import preserves streaming.service's replaceable integration-test boundary
    # without creating a module-import cycle for callers that enqueue from that service.
    from app.streaming.service import livekit_control_provider

    return livekit_control_provider()


async def process_next_live_provider_control_intent(
    *,
    success_hook: LiveProviderControlSuccessHook,
    intent_id: UUID | None = None,
    session_factory: SessionFactory = SessionLocal,
    now: datetime | None = None,
    provider_factory: ProviderFactory | None = None,
) -> LiveProviderControlProcessResult | None:
    """Claim one committed command, commit its lease, then invoke LiveKit."""

    current = _now(now)
    claim = await _claim_next_intent(
        intent_id=intent_id,
        session_factory=session_factory,
        current=current,
    )
    if claim is None:
        return None
    if not claim.provider_invoked:
        return LiveProviderControlProcessResult(claim.intent, provider_invoked=False)

    structural_error = _structural_error(claim.intent)
    if structural_error is not None:
        terminal = await _finalize_structural_failure(
            intent_id=claim.intent.id,
            expected_attempt_count=claim.intent.attempt_count,
            error_code=structural_error,
            session_factory=session_factory,
            current=current,
        )
        return LiveProviderControlProcessResult(terminal, provider_invoked=False)

    try:
        provider = (provider_factory or _default_provider_factory)()
        if claim.intent.action is LiveProviderControlAction.delete_room:
            await provider.close_room(claim.intent.provider_room_name)
        else:
            assert claim.intent.participant_identity is not None
            await provider.remove_participant(
                claim.intent.provider_room_name,
                claim.intent.participant_identity,
            )
    except Exception as exc:  # noqa: BLE001 - the durable boundary must retain every failure
        pending = await _finalize_retryable_failure(
            intent_id=claim.intent.id,
            expected_attempt_count=claim.intent.attempt_count,
            error_code=_provider_error_code(exc),
            session_factory=session_factory,
            current=current,
        )
        return LiveProviderControlProcessResult(pending, provider_invoked=True)

    try:
        succeeded = await _finalize_successful_intent(
            intent_id=claim.intent.id,
            expected_attempt_count=claim.intent.attempt_count,
            success_hook=success_hook,
            session_factory=session_factory,
            current=current,
        )
    except LiveProviderControlStructuralError as exc:
        terminal = await _finalize_structural_failure(
            intent_id=claim.intent.id,
            expected_attempt_count=claim.intent.attempt_count,
            error_code=exc.error_code,
            session_factory=session_factory,
            current=current,
        )
        return LiveProviderControlProcessResult(terminal, provider_invoked=True)
    return LiveProviderControlProcessResult(succeeded, provider_invoked=True)


async def process_due_live_provider_control_intents(
    *,
    success_hook: LiveProviderControlSuccessHook,
    limit: int = LIVE_PROVIDER_CONTROL_BATCH_LIMIT,
    session_factory: SessionFactory = SessionLocal,
    now: datetime | None = None,
    provider_factory: ProviderFactory | None = None,
) -> LiveProviderControlBatchResult:
    """Process a bounded due batch; durable row state, not task retries, owns replay."""

    if not 1 <= limit <= LIVE_PROVIDER_CONTROL_MAX_BATCH_LIMIT:
        raise LiveProviderControlError(
            f"limit must be between 1 and {LIVE_PROVIDER_CONTROL_MAX_BATCH_LIMIT}"
        )
    current = _now(now)
    results: list[LiveProviderControlProcessResult] = []
    for _ in range(limit):
        result = await process_next_live_provider_control_intent(
            success_hook=success_hook,
            session_factory=session_factory,
            now=current,
            provider_factory=provider_factory,
        )
        if result is None:
            break
        results.append(result)
    return LiveProviderControlBatchResult(tuple(results))
