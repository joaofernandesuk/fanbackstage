import asyncio
import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.db.session import SessionLocal
from app.integrations.streaming import StreamingProviderError
from app.models.audit import AuditEvent
from app.models.identity import User
from app.models.streaming import (
    LiveProviderControlAction,
    LiveProviderControlIntent,
    LiveProviderControlStatus,
)
from app.streaming.control_outbox import (
    LIVE_PROVIDER_CONTROL_LEASE_SECONDS,
    LIVE_PROVIDER_CONTROL_RETRY_BASE_SECONDS,
    LiveProviderControlError,
    enqueue_live_provider_control_intent,
    process_due_live_provider_control_intents,
    process_next_live_provider_control_intent,
)
from app.worker.celery_app import celery_app


async def _noop_success_hook(_db, _intent) -> None:
    pass


async def _process_next(**kwargs):
    return await process_next_live_provider_control_intent(
        success_hook=_noop_success_hook,
        **kwargs,
    )


async def _process_due(**kwargs):
    return await process_due_live_provider_control_intents(
        success_hook=_noop_success_hook,
        **kwargs,
    )


async def _actor_id() -> UUID | None:
    async with SessionLocal() as db:
        return await db.scalar(
            select(User.id).where(User.email == "compliance-policy-fixture@example.test")
        )


async def _enqueue(
    *,
    action: LiveProviderControlAction = LiveProviderControlAction.delete_room,
    key: str,
    room: str,
    participant_identity: str | None = None,
    actor_user_id: UUID | None = None,
) -> UUID:
    async with SessionLocal() as db:
        intent, created = await enqueue_live_provider_control_intent(
            db,
            action=action,
            target_type="live_room",
            target_id=f"target:{key}",
            provider_room_name=room,
            participant_identity=participant_identity,
            reason="enforce_authoritative_live_state",
            actor_user_id=actor_user_id,
            idempotency_key=key,
        )
        assert created is True
        intent_id = intent.id
        await db.commit()
        return intent_id


def test_0039_migration_chain_and_retained_intent_downgrade_guard(monkeypatch) -> None:
    migration_path = (
        Path(__file__).parents[1] / "alembic/versions/20260827_0039_live_provider_control_outbox.py"
    )
    spec = importlib.util.spec_from_file_location(
        "live_provider_control_migration_0039", migration_path
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "20260827_0039"
    assert migration.down_revision == "20260827_0038"

    class _RetainedIntentBind:
        @staticmethod
        def scalar(_statement):
            return True

    monkeypatch.setattr(migration.op, "get_bind", _RetainedIntentBind)
    with pytest.raises(RuntimeError, match="durable LiveKit provider-control intents exist"):
        migration.downgrade()


async def test_0039_schema_has_exact_enums_constraints_and_due_index(db_session) -> None:
    action_values = tuple(
        await db_session.scalars(
            text(
                "SELECT enumlabel FROM pg_enum "
                "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
                "WHERE typname = 'live_provider_control_action' ORDER BY enumsortorder"
            )
        )
    )
    status_values = tuple(
        await db_session.scalars(
            text(
                "SELECT enumlabel FROM pg_enum "
                "JOIN pg_type ON pg_type.oid = pg_enum.enumtypid "
                "WHERE typname = 'live_provider_control_status' ORDER BY enumsortorder"
            )
        )
    )
    constraints = set(
        await db_session.scalars(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'live_provider_control_intents'::regclass"
            )
        )
    )
    indexes = set(
        await db_session.scalars(
            text(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'live_provider_control_intents'"
            )
        )
    )
    assert action_values == ("delete_room", "remove_participant")
    assert status_values == ("pending", "processing", "succeeded", "failed_terminal")
    assert {
        "ck_live_provider_control_action_target",
        "ck_live_provider_control_required_text",
        "ck_live_provider_control_attempt_count",
        "ck_live_provider_control_error_pair",
        "ck_live_provider_control_status_state",
        "uq_live_provider_control_intents_idempotency_key",
    } <= constraints
    assert "ix_live_provider_control_due" in indexes


async def test_enqueue_is_transactional_exact_and_idempotent(livekit_control) -> None:
    actor_user_id = await _actor_id()
    assert actor_user_id is not None
    current = datetime.now(UTC) + timedelta(seconds=1)
    async with SessionLocal() as producer:
        intent, created = await enqueue_live_provider_control_intent(
            producer,
            action="delete_room",
            target_type="live_room",
            target_id="room-public-id",
            provider_room_name="provider-room-transactional",
            reason="creator_suspended",
            actor_user_id=actor_user_id,
            idempotency_key="delete-room:transactional",
        )
        assert created is True
        intent_id = intent.id

        # The processor owns a new session, so the creating transaction must commit first.
        assert await _process_next(intent_id=intent_id, now=current) is None
        assert livekit_control.closed_rooms == []
        await producer.commit()

    result = await _process_next(intent_id=intent_id, now=current)
    assert result is not None
    assert result.provider_invoked is True
    assert result.intent.status is LiveProviderControlStatus.succeeded
    assert result.intent.actor_user_id == actor_user_id
    assert livekit_control.closed_rooms == ["provider-room-transactional"]

    async with SessionLocal() as replay:
        same, replay_created = await enqueue_live_provider_control_intent(
            replay,
            action=LiveProviderControlAction.delete_room,
            target_type="live_room",
            target_id="room-public-id",
            provider_room_name="provider-room-transactional",
            reason="creator_suspended",
            actor_user_id=actor_user_id,
            idempotency_key="delete-room:transactional",
        )
        assert replay_created is False
        assert same.id == intent_id
        with pytest.raises(LiveProviderControlError, match="different provider-control command"):
            await enqueue_live_provider_control_intent(
                replay,
                action=LiveProviderControlAction.delete_room,
                target_type="live_room",
                target_id="room-public-id",
                provider_room_name="different-room",
                reason="creator_suspended",
                actor_user_id=actor_user_id,
                idempotency_key="delete-room:transactional",
            )

    async with SessionLocal() as audit_db:
        event = await audit_db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "streaming.provider_control_succeeded",
                AuditEvent.target_id == str(intent_id),
            )
        )
        assert event is not None
        assert event.actor_user_id == actor_user_id
        assert event.metadata_json["provider_room_name"] == "provider-room-transactional"
        assert event.metadata_json["reason"] == "creator_suspended"


async def test_enqueue_rejects_structurally_invalid_commands(db_session) -> None:
    with pytest.raises(LiveProviderControlError, match="cannot include participant_identity"):
        await enqueue_live_provider_control_intent(
            db_session,
            action=LiveProviderControlAction.delete_room,
            target_type="live_room",
            target_id="target",
            provider_room_name="room",
            participant_identity="user:forbidden",
            reason="moderation",
            idempotency_key="invalid-delete",
        )
    with pytest.raises(LiveProviderControlError, match="participant_identity must be text"):
        await enqueue_live_provider_control_intent(
            db_session,
            action=LiveProviderControlAction.remove_participant,
            target_type="user",
            target_id="target",
            provider_room_name="room",
            reason="authority_revoked",
            idempotency_key="invalid-remove",
        )


async def test_provider_failure_stays_retryable_then_succeeds_with_audit(monkeypatch) -> None:
    class _FailsOnceProvider:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def close_room(self, room_name: str) -> None:
            self.calls.append(room_name)
            if len(self.calls) == 1:
                raise StreamingProviderError("transient provider failure")

    provider = _FailsOnceProvider()
    from app.streaming import service as streaming_service

    monkeypatch.setattr(streaming_service, "livekit_control_provider", lambda: provider)
    intent_id = await _enqueue(key="delete-room:retry", room="provider-room-retry")
    first_at = datetime.now(UTC) + timedelta(seconds=1)
    first = await _process_next(intent_id=intent_id, now=first_at)
    assert first is not None
    assert first.intent.status is LiveProviderControlStatus.pending
    assert first.intent.retryable is True
    assert first.intent.attempt_count == 1
    assert first.intent.last_error_code == "PROVIDER_STREAMINGPROVIDERERROR"
    assert first.intent.next_attempt_at == first_at + timedelta(
        seconds=LIVE_PROVIDER_CONTROL_RETRY_BASE_SECONDS
    )
    assert first.intent.terminal_failed_at is None

    assert (
        await _process_next(
            intent_id=intent_id,
            now=first.intent.next_attempt_at - timedelta(microseconds=1),
        )
        is None
    )
    second = await _process_next(
        intent_id=intent_id,
        now=first.intent.next_attempt_at,
    )
    assert second is not None and second.succeeded
    assert second.intent.attempt_count == 2
    assert second.intent.last_error_code == "PROVIDER_STREAMINGPROVIDERERROR"
    assert provider.calls == ["provider-room-retry", "provider-room-retry"]

    async with SessionLocal() as audit_db:
        event_types = list(
            await audit_db.scalars(
                select(AuditEvent.event_type)
                .where(AuditEvent.target_id == str(intent_id))
                .order_by(AuditEvent.created_at, AuditEvent.id)
            )
        )
    assert event_types == [
        "streaming.provider_control_retry_scheduled",
        "streaming.provider_control_succeeded",
    ]


async def test_provider_success_then_final_db_failure_replays_idempotently(monkeypatch) -> None:
    class _IdempotentProvider:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def remove_participant(self, room_name: str, identity: str) -> None:
            self.calls.append((room_name, identity))

    provider = _IdempotentProvider()
    from app.streaming import service as streaming_service

    monkeypatch.setattr(streaming_service, "livekit_control_provider", lambda: provider)
    intent_id = await _enqueue(
        action=LiveProviderControlAction.remove_participant,
        key="remove-participant:db-finalize-retry",
        room="provider-room-finalize-retry",
        participant_identity="user:exact-provider-identity",
    )
    hook_attempts: list[int] = []

    async def domain_success_hook(db, intent) -> None:
        hook_attempts.append(intent.attempt_count)
        await record_event(
            db,
            "test.live_provider_domain_finalized",
            target_type="live_provider_control_intent",
            target_id=str(intent.id),
        )

    original_commit = AsyncSession.commit
    commit_calls = 0

    async def fail_first_finalization_commit(session):
        nonlocal commit_calls
        commit_calls += 1
        if commit_calls == 2:
            raise RuntimeError("simulated final database update failure")
        return await original_commit(session)

    monkeypatch.setattr(AsyncSession, "commit", fail_first_finalization_commit)
    first_at = datetime.now(UTC) + timedelta(seconds=1)
    with pytest.raises(RuntimeError, match="simulated final database update failure"):
        await process_next_live_provider_control_intent(
            success_hook=domain_success_hook,
            intent_id=intent_id,
            now=first_at,
        )

    async with SessionLocal() as db:
        stranded = await db.get(LiveProviderControlIntent, intent_id)
        assert stranded is not None
        assert stranded.status is LiveProviderControlStatus.processing
        assert stranded.attempt_count == 1
        assert stranded.lease_expires_at == first_at + timedelta(
            seconds=LIVE_PROVIDER_CONTROL_LEASE_SECONDS
        )

    retry_at = first_at + timedelta(seconds=LIVE_PROVIDER_CONTROL_LEASE_SECONDS)
    recovered = await process_next_live_provider_control_intent(
        success_hook=domain_success_hook,
        intent_id=intent_id,
        now=retry_at,
    )
    assert recovered is not None and recovered.succeeded
    assert recovered.intent.attempt_count == 2
    assert provider.calls == [
        ("provider-room-finalize-retry", "user:exact-provider-identity"),
        ("provider-room-finalize-retry", "user:exact-provider-identity"),
    ]

    already_complete = await process_next_live_provider_control_intent(
        success_hook=domain_success_hook,
        intent_id=intent_id,
        now=retry_at + timedelta(seconds=1),
    )
    assert already_complete is not None and already_complete.succeeded
    assert already_complete.provider_invoked is False
    assert len(provider.calls) == 2
    assert hook_attempts == [1, 2]

    async with SessionLocal() as audit_db:
        success_count = await audit_db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.event_type == "streaming.provider_control_succeeded",
                AuditEvent.target_id == str(intent_id),
            )
        )
        hook_count = await audit_db.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(
                AuditEvent.event_type == "test.live_provider_domain_finalized",
                AuditEvent.target_id == str(intent_id),
            )
        )
    assert success_count == 1
    assert hook_count == 1


async def test_delayed_older_success_cannot_override_newer_retry_state(monkeypatch) -> None:
    class _OverlappingProvider:
        def __init__(self) -> None:
            self.calls = 0
            self.first_started = asyncio.Event()
            self.release_first = asyncio.Event()

        async def close_room(self, _room_name: str) -> None:
            self.calls += 1
            if self.calls == 1:
                self.first_started.set()
                await self.release_first.wait()
                return
            raise StreamingProviderError("newer attempt failed")

    provider = _OverlappingProvider()
    from app.streaming import service as streaming_service

    monkeypatch.setattr(streaming_service, "livekit_control_provider", lambda: provider)
    intent_id = await _enqueue(key="delete-room:attempt-fence", room="provider-room-fence")
    first_at = datetime.now(UTC) + timedelta(seconds=1)
    hook_attempts: list[int] = []

    async def domain_success_hook(_db, intent) -> None:
        hook_attempts.append(intent.attempt_count)

    first_task = asyncio.create_task(
        process_next_live_provider_control_intent(
            success_hook=domain_success_hook,
            intent_id=intent_id,
            now=first_at,
        )
    )
    await provider.first_started.wait()
    second_at = first_at + timedelta(seconds=LIVE_PROVIDER_CONTROL_LEASE_SECONDS)
    second = await process_next_live_provider_control_intent(
        success_hook=domain_success_hook,
        intent_id=intent_id,
        now=second_at,
    )
    assert second is not None
    assert second.intent.status is LiveProviderControlStatus.pending
    assert second.intent.attempt_count == 2
    assert second.intent.retryable is True

    provider.release_first.set()
    delayed_first = await first_task
    assert delayed_first is not None
    assert delayed_first.intent.status is LiveProviderControlStatus.pending
    assert delayed_first.intent.attempt_count == 2
    assert delayed_first.intent.last_error_code == "PROVIDER_STREAMINGPROVIDERERROR"
    assert hook_attempts == []

    async with SessionLocal() as db:
        persisted = await db.get(LiveProviderControlIntent, intent_id)
        assert persisted is not None
        assert persisted.status is LiveProviderControlStatus.pending
        assert persisted.attempt_count == 2
        assert persisted.succeeded_at is None


async def test_claim_uses_skip_locked_and_batch_is_bounded(livekit_control) -> None:
    first_id = await _enqueue(key="delete-room:locked-first", room="provider-room-locked")
    second_id = await _enqueue(key="delete-room:second", room="provider-room-second")
    current = datetime.now(UTC) + timedelta(seconds=5)
    async with SessionLocal() as ordering:
        first = await ordering.get(LiveProviderControlIntent, first_id)
        second = await ordering.get(LiveProviderControlIntent, second_id)
        assert first is not None and second is not None
        first.next_attempt_at = current - timedelta(seconds=2)
        second.next_attempt_at = current - timedelta(seconds=1)
        await ordering.commit()

    async with SessionLocal() as locker:
        await locker.scalar(
            select(LiveProviderControlIntent)
            .where(LiveProviderControlIntent.id == first_id)
            .with_for_update()
        )
        result = await _process_next(now=current)
        assert result is not None
        assert result.intent.id == second_id
        assert livekit_control.closed_rooms == ["provider-room-second"]
        await locker.rollback()

    batch = await _process_due(limit=1, now=current)
    assert batch.processed_count == 1
    assert batch.succeeded_count == 1
    assert batch.succeeded_intents[0].id == first_id
    with pytest.raises(LiveProviderControlError, match="limit must be between"):
        await _process_due(limit=0, now=current)


def test_worker_routes_and_schedules_bounded_provider_control_replay() -> None:
    assert celery_app.conf.task_routes["app.worker.tasks.process_live_provider_control_outbox"] == {
        "queue": "scheduled"
    }
    assert celery_app.conf.beat_schedule["live-provider-control-outbox"] == {
        "task": "app.worker.tasks.process_live_provider_control_outbox",
        "schedule": 10.0,
    }
