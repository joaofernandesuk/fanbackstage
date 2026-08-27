import json
from base64 import b64encode, urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import new as hmac_new

import pytest
from conftest import trusted_self_attested_accounts as accounts
from sqlalchemy import select

from app.core.config import get_settings
from app.creators import service as creators
from app.finance import service as finance
from app.integrations.streaming import LiveKitStreamingProvider
from app.models.audit import AuditEvent
from app.models.creator import CreatorStatus
from app.models.finance import (
    LedgerDirection,
    LedgerEntry,
    LedgerTransaction,
    LedgerTransactionType,
    PaymentAttempt,
    PaymentRefundRequirement,
    PaymentStatus,
)
from app.models.messaging import UserBlock
from app.models.streaming import (
    LiveAccessMode,
    LiveRecording,
    LiveRecordingStatus,
    LiveReport,
    PrivateRequestStatus,
    PrivateSession,
    PrivateSessionMode,
    PrivateSessionRequest,
    PrivateSessionSettlement,
    PrivateSessionStatus,
    SessionParticipant,
)
from app.streaming import service as streaming


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(1, 5), (30, 150), (60, 300), (61, 305), (95, 475)],
)
def test_second_level_billing_rounds_minor_units_deterministically(seconds, expected):
    """ceil(rate * seconds / 60), without per-minute rounding or floats."""

    class Session:
        per_minute_price_minor = 300
        billable_seconds = seconds
        minimum_charge_minor = 1
        max_authorization_minor = 10_000

    assert streaming.settlement_amount(Session()) == expected


def signed_livekit_webhook(body: bytes) -> str:
    header = urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').rstrip(b"=").decode()
    claims = {
        "iss": "devkey",
        "exp": int((datetime.now(UTC) + timedelta(minutes=1)).timestamp()),
        "sha256": b64encode(sha256(body).digest()).decode(),
    }
    payload = (
        urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode()).rstrip(b"=").decode()
    )
    signature = (
        urlsafe_b64encode(
            hmac_new(
                b"fanbackstage-livekit-development-secret-2026",
                f"{header}.{payload}".encode(),
                sha256,
            ).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    return f"{header}.{payload}.{signature}"


def signed_payment_event(
    attempt: PaymentAttempt, event_type: str, event_id: str
) -> tuple[bytes, str]:
    payload = json.dumps(
        {
            "id": event_id,
            "type": event_type,
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    signature = hmac_new(
        get_settings().payment_webhook_secret.encode(), payload, sha256
    ).hexdigest()
    return payload, signature


def test_livekit_webhook_requires_valid_signature_and_raw_body_hash():
    body = b'{"id":"event-1","event":"participant_joined"}'
    provider = LiveKitStreamingProvider()
    assert provider.verify_webhook(body, signed_livekit_webhook(body))["id"] == "event-1"
    with pytest.raises(ValueError, match="authorization"):
        provider.verify_webhook(body, None)
    with pytest.raises(ValueError, match="authorization"):
        provider.verify_webhook(body, "Bearer invalid")
    with pytest.raises(ValueError, match="authorization"):
        provider.verify_webhook(body, f"Bearer {signed_livekit_webhook(body)}")
    with pytest.raises(ValueError, match="hash"):
        provider.verify_webhook(b"{}", signed_livekit_webhook(body))


async def creator(db, email):
    user, _ = await accounts.register(db, email, "strong-password-123", None)
    profile = await creators.get_or_create_profile(db, user)
    await creators.update_profile(
        db, profile, {"username": email.split("@")[0], "display_name": "Creator"}, user.id
    )
    await creators.submit(db, profile, user.id)
    await creators.development_verify(db, profile, True, user.id)
    await creators.set_status(db, profile, CreatorStatus.approved, user.id)
    profile.is_public = True
    return user, profile


@pytest.mark.asyncio
async def test_private_requests_queue_during_live_but_cannot_be_accepted_until_live_ends(
    db_session,
):
    owner, profile = await creator(db_session, "stream-owner@example.com")
    viewer, _ = await accounts.register(
        db_session, "stream-viewer@example.com", "strong-password-123", None
    )
    room = await streaming.start_live(
        db_session, owner, "Live now", streaming.LiveAccessMode.public
    )
    queued = await streaming.request_private_session(
        db_session, viewer, profile.id, PrivateSessionMode.one_to_one
    )

    with pytest.raises(streaming.StreamingError, match="End the public live"):
        await streaming.accept_private_request(db_session, owner, queued.id)
    assert (
        await db_session.scalar(
            select(PrivateSession).where(PrivateSession.request_id == queued.id)
        )
        is None
    )

    await streaming.end_live(db_session, owner, room.id)
    session = await streaming.accept_private_request(db_session, owner, queued.id)
    assert session.status is PrivateSessionStatus.awaiting_payment_authorization
    assert session.payment_attempt_id is not None
    assert session.provider_room_name


@pytest.mark.parametrize("containment", ["private", "requester_blocks", "creator_blocks"])
@pytest.mark.asyncio
async def test_stale_private_request_rechecks_creator_and_relationship_before_payment(
    db_session, containment
):
    slug = {"private": "p", "requester_blocks": "rb", "creator_blocks": "cb"}[containment]
    owner, profile = await creator(db_session, f"sp-{slug}@example.com")
    requester, _ = await accounts.register(
        db_session,
        f"sv-{slug}@example.com",
        "strong-password-123",
        None,
    )
    request = await streaming.request_private_session(
        db_session, requester, profile.id, PrivateSessionMode.one_to_one
    )
    if containment == "private":
        profile.is_public = False
    else:
        db_session.add(
            UserBlock(
                blocker_user_id=(requester.id if containment == "requester_blocks" else owner.id),
                blocked_user_id=(owner.id if containment == "requester_blocks" else requester.id),
            )
        )
    await db_session.flush()

    with pytest.raises(PermissionError, match="Private session request not found"):
        await streaming.accept_private_request(db_session, owner, request.id)
    assert request.status is PrivateRequestStatus.pending
    assert await db_session.scalar(select(PaymentAttempt.id)) is None
    assert await db_session.scalar(select(PrivateSession.id)) is None


@pytest.mark.parametrize("containment", ["private", "requester_blocks", "creator_blocks"])
@pytest.mark.asyncio
async def test_private_request_requires_public_unblocked_creator(db_session, containment):
    slug = {"private": "p", "requester_blocks": "rb", "creator_blocks": "cb"}[containment]
    owner, profile = await creator(db_session, f"np-{slug}@example.com")
    requester, _ = await accounts.register(
        db_session,
        f"nv-{slug}@example.com",
        "strong-password-123",
        None,
    )
    if containment == "private":
        profile.is_public = False
    else:
        db_session.add(
            UserBlock(
                blocker_user_id=(requester.id if containment == "requester_blocks" else owner.id),
                blocked_user_id=(owner.id if containment == "requester_blocks" else requester.id),
            )
        )
    await db_session.flush()

    with pytest.raises(PermissionError, match="Private session is unavailable"):
        await streaming.request_private_session(
            db_session, requester, profile.id, PrivateSessionMode.one_to_one
        )
    assert await db_session.scalar(select(PrivateSessionRequest.id)) is None
    assert await db_session.scalar(select(PaymentAttempt.id)) is None


@pytest.mark.asyncio
async def test_private_presence_reconciliation_uses_livekit_membership_for_delayed_joins_and_leaves(
    db_session, monkeypatch
):
    """A missed callback is repaired from LiveKit, never from browser state."""
    owner, profile = await creator(db_session, "presence-owner@example.com")
    payer, _ = await accounts.register(
        db_session, "presence-payer@example.com", "strong-password-123", None
    )
    request = await streaming.request_private_session(
        db_session, payer, profile.id, PrivateSessionMode.one_to_one
    )
    session = await streaming.accept_private_request(db_session, owner, request.id)
    session.status = PrivateSessionStatus.ready

    class Provider:
        def __init__(self) -> None:
            self.identities = {str(owner.id), str(payer.id)}

        async def list_participant_identities(self, room_name: str) -> set[str]:
            assert room_name == session.provider_room_name
            return self.identities

    provider = Provider()
    monkeypatch.setattr(streaming, "LiveKitStreamingProvider", lambda: provider)

    assert await streaming.reconcile_private_provider_presence(db_session) == 2
    assert session.status is PrivateSessionStatus.active
    participants = (
        await db_session.scalars(
            select(SessionParticipant).where(SessionParticipant.private_session_id == session.id)
        )
    ).all()
    assert all(
        participant.joined_at and participant.left_at is None for participant in participants
    )

    provider.identities = {str(owner.id)}
    assert await streaming.reconcile_private_provider_presence(db_session) == 1
    assert session.status is PrivateSessionStatus.reconnecting


@pytest.mark.asyncio
async def test_private_presence_reconciliation_does_not_disconnect_unjoined_ready_participants(
    db_session, monkeypatch
):
    owner, profile = await creator(db_session, "presence-ready-owner@example.com")
    payer, _ = await accounts.register(
        db_session, "presence-ready-payer@example.com", "strong-password-123", None
    )
    request = await streaming.request_private_session(
        db_session, payer, profile.id, PrivateSessionMode.one_to_one
    )
    session = await streaming.accept_private_request(db_session, owner, request.id)
    session.status = PrivateSessionStatus.ready

    class Provider:
        async def list_participant_identities(self, room_name: str) -> set[str]:
            return set()

    monkeypatch.setattr(streaming, "LiveKitStreamingProvider", Provider)
    assert await streaming.reconcile_private_provider_presence(db_session) == 0
    assert session.status is PrivateSessionStatus.ready


@pytest.mark.asyncio
async def test_two_to_one_snapshots_separate_rate_and_specific_invitee(db_session):
    owner, profile = await creator(db_session, "two-owner@example.com")
    payer, _ = await accounts.register(
        db_session, "two-payer@example.com", "strong-password-123", None
    )
    invited, _ = await accounts.register(
        db_session, "two-invited@example.com", "strong-password-123", None
    )
    settings = await streaming.settings_for_creator(db_session, profile.id)
    settings.one_to_one_price_minor, settings.two_to_one_price_minor = 100, 275
    request = await streaming.request_private_session(
        db_session, payer, profile.id, PrivateSessionMode.two_to_one, invited.id
    )
    assert request.per_minute_price_minor == 275
    assert request.requester_user_id == payer.id and request.invited_user_id == invited.id
    with pytest.raises(streaming.StreamingError, match="specific second viewer"):
        await streaming.request_private_session(
            db_session, payer, profile.id, PrivateSessionMode.two_to_one
        )
    session = await streaming.accept_private_request(db_session, owner, request.id)
    participants = (
        await db_session.scalars(
            select(streaming.SessionParticipant).where(
                streaming.SessionParticipant.private_session_id == session.id
            )
        )
    ).all()
    assert {participant.user_id for participant in participants} == {owner.id, payer.id, invited.id}


@pytest.mark.asyncio
async def test_two_to_one_real_lifecycle_has_one_payer_timer_and_settlement(db_session):
    owner, profile = await creator(db_session, "waiting-owner@example.com")
    payer, _ = await accounts.register(
        db_session, "waiting-payer@example.com", "strong-password-123", None
    )
    invited, _ = await accounts.register(
        db_session, "waiting-invited@example.com", "strong-password-123", None
    )
    stranger, _ = await accounts.register(
        db_session, "waiting-stranger@example.com", "strong-password-123", None
    )
    request = await streaming.request_private_session(
        db_session, payer, profile.id, PrivateSessionMode.two_to_one, invited.id
    )
    session = await streaming.accept_private_request(db_session, owner, request.id)
    assert session.mode is PrivateSessionMode.two_to_one
    assert session.payer_user_id == payer.id
    assert session.per_minute_price_minor == request.per_minute_price_minor
    with pytest.raises(PermissionError, match="unavailable"):
        await streaming.issue_private_token(db_session, payer, session.id)

    attempt = await db_session.get(PaymentAttempt, session.payment_attempt_id)
    assert attempt is not None and attempt.buyer_user_id == payer.id
    attempt.status = PaymentStatus.succeeded
    await streaming.authorize_private_session(db_session, session)
    assert session.status is PrivateSessionStatus.ready

    with pytest.raises(PermissionError, match="not invited"):
        await streaming.issue_private_token(db_session, stranger, session.id)
    for participant in (owner, payer, invited):
        _, token = await streaming.issue_private_token(db_session, participant, session.id)
        encoded_claims = token.split(".")[1]
        payload = json.loads(urlsafe_b64decode(encoded_claims + "=" * (-len(encoded_claims) % 4)))
        assert payload["sub"] == str(participant.id)
        assert payload["video"]["room"] == session.provider_room_name
        assert payload["video"]["roomJoin"] is True
        assert payload["video"]["canPublish"] is True

    start = datetime(2026, 8, 21, tzinfo=UTC)
    await streaming.private_participant_connected(db_session, owner, session.id, start)
    await streaming.private_participant_connected(db_session, payer, session.id, start)
    assert session.status is PrivateSessionStatus.connecting
    assert session.billable_seconds == 0
    await streaming.private_participant_connected(db_session, invited, session.id, start)
    assert session.status is PrivateSessionStatus.active
    await streaming.private_participant_disconnected(
        db_session, invited, session.id, start + timedelta(seconds=30)
    )
    assert session.status is PrivateSessionStatus.reconnecting
    assert session.billable_seconds == 30
    await streaming.private_participant_connected(
        db_session, invited, session.id, start + timedelta(seconds=45)
    )
    assert session.status is PrivateSessionStatus.active
    settled = await streaming.end_private_session(
        db_session, owner, session.id, "ended_by_creator", start + timedelta(seconds=75)
    )
    assert settled.status is PrivateSessionStatus.settled
    assert settled.mode is PrivateSessionMode.two_to_one
    assert settled.billable_seconds == 60
    assert await db_session.scalar(
        select(LedgerTransaction).where(
            LedgerTransaction.reference == f"private_session:{session.id}"
        )
    )
    assert (
        await streaming.end_private_session(db_session, owner, session.id, "replay")
    ).status is (PrivateSessionStatus.settled)
    assert (
        len(
            (
                await db_session.scalars(
                    select(LedgerTransaction).where(
                        LedgerTransaction.reference == f"private_session:{session.id}"
                    )
                )
            ).all()
        )
        == 1
    )


@pytest.mark.asyncio
async def test_disconnect_pauses_billable_time_and_reconnect_resumes(db_session):
    owner, profile = await creator(db_session, "reconnect-owner@example.com")
    payer, _ = await accounts.register(
        db_session, "reconnect-payer@example.com", "strong-password-123", None
    )
    request = await streaming.request_private_session(
        db_session, payer, profile.id, PrivateSessionMode.one_to_one
    )
    session = await streaming.accept_private_request(db_session, owner, request.id)
    session.status = PrivateSessionStatus.ready
    start = datetime(2026, 8, 21, tzinfo=UTC)
    await streaming.private_participant_connected(db_session, owner, session.id, start)
    await streaming.private_participant_connected(db_session, payer, session.id, start)
    assert session.status is PrivateSessionStatus.active
    await streaming.private_participant_disconnected(
        db_session, payer, session.id, start + timedelta(seconds=20)
    )
    assert session.status is PrivateSessionStatus.reconnecting and session.billable_seconds == 20
    await streaming.private_participant_disconnected(
        db_session, payer, session.id, start + timedelta(seconds=25)
    )
    assert session.billable_seconds == 20
    await streaming.private_participant_connected(
        db_session, payer, session.id, start + timedelta(seconds=30)
    )
    assert session.status is PrivateSessionStatus.active


@pytest.mark.asyncio
async def test_reconciliation_only_authorizes_verified_private_payment(db_session):
    owner, profile = await creator(db_session, "authorization-owner@example.com")
    payer, _ = await accounts.register(
        db_session, "authorization-payer@example.com", "strong-password-123", None
    )
    request = await streaming.request_private_session(
        db_session, payer, profile.id, PrivateSessionMode.one_to_one
    )
    session = await streaming.accept_private_request(db_session, owner, request.id)
    assert session.status is PrivateSessionStatus.awaiting_payment_authorization
    assert await streaming.reconcile_private_authorizations(db_session) == 0
    attempt = await db_session.get(PaymentAttempt, session.payment_attempt_id)
    assert attempt is not None
    attempt.status = PaymentStatus.succeeded
    assert await streaming.reconcile_private_authorizations(db_session) == 1
    assert session.status is PrivateSessionStatus.ready
    assert await streaming.reconcile_private_authorizations(db_session) == 0


@pytest.mark.asyncio
async def test_provider_event_replay_cannot_inflate_private_billable_time(db_session):
    owner, profile = await creator(db_session, "event-owner@example.com")
    payer, _ = await accounts.register(
        db_session, "event-payer@example.com", "strong-password-123", None
    )
    request = await streaming.request_private_session(
        db_session, payer, profile.id, PrivateSessionMode.one_to_one
    )
    session = await streaming.accept_private_request(db_session, owner, request.id)
    session.status = PrivateSessionStatus.ready
    now = datetime(2026, 8, 21, tzinfo=UTC)
    await streaming.process_private_provider_event(
        db_session,
        event_id="join-owner",
        event_type="participant_joined",
        session_id=session.id,
        user_id=owner.id,
        now=now,
    )
    await streaming.process_private_provider_event(
        db_session,
        event_id="join-payer",
        event_type="participant_joined",
        session_id=session.id,
        user_id=payer.id,
        now=now,
    )
    await streaming.process_private_provider_event(
        db_session,
        event_id="leave-payer",
        event_type="participant_left",
        session_id=session.id,
        user_id=payer.id,
        now=now + timedelta(seconds=15),
    )
    assert session.billable_seconds == 15
    assert (
        await streaming.process_private_provider_event(
            db_session,
            event_id="leave-payer",
            event_type="participant_left",
            session_id=session.id,
            user_id=payer.id,
            now=now + timedelta(seconds=45),
        )
        is None
    )
    assert session.billable_seconds == 15


@pytest.mark.asyncio
async def test_terminal_private_session_rejects_delayed_provider_events_and_reconciliation(
    db_session,
):
    owner, profile = await creator(db_session, "terminal-event-owner@example.com")
    payer, _ = await accounts.register(
        db_session, "terminal-event-payer@example.com", "strong-password-123", None
    )
    request = await streaming.request_private_session(
        db_session, payer, profile.id, PrivateSessionMode.one_to_one
    )
    session = await streaming.accept_private_request(db_session, owner, request.id)
    session.status = PrivateSessionStatus.active
    session.active_started_at = datetime(2026, 8, 21, tzinfo=UTC)
    await streaming.end_private_session(
        db_session,
        owner,
        session.id,
        "ended_by_participant",
        datetime(2026, 8, 21, 0, 0, 1, tzinfo=UTC),
    )
    assert session.status is PrivateSessionStatus.settled

    await streaming.process_private_provider_event(
        db_session,
        event_id="late-leave-after-settlement",
        event_type="participant_left",
        session_id=session.id,
        user_id=payer.id,
        now=datetime(2026, 8, 21, 0, 0, 2, tzinfo=UTC),
    )
    await streaming.process_private_provider_event(
        db_session,
        event_id="late-join-after-settlement",
        event_type="participant_joined",
        session_id=session.id,
        user_id=payer.id,
        now=datetime(2026, 8, 21, 0, 0, 3, tzinfo=UTC),
    )
    assert (
        await streaming.process_private_provider_event(
            db_session,
            event_id="late-join-after-settlement",
            event_type="participant_joined",
            session_id=session.id,
            user_id=payer.id,
            now=datetime(2026, 8, 21, 0, 0, 4, tzinfo=UTC),
        )
        is None
    )

    assert session.status is PrivateSessionStatus.settled
    assert session.billable_seconds == 1
    # Terminal sessions are excluded from provider reconciliation, so a room
    # that happens to contain a stale participant can never reopen settlement.
    assert await streaming.reconcile_private_provider_presence(db_session) == 0


@pytest.mark.asyncio
async def test_private_settlement_uses_seconds_minimum_cap_and_is_idempotent(db_session):
    owner, profile = await creator(db_session, "settle-owner@example.com")
    payer, _ = await accounts.register(
        db_session, "settle-payer@example.com", "strong-password-123", None
    )
    settings = await streaming.settings_for_creator(db_session, profile.id)
    settings.one_to_one_price_minor, settings.minimum_minutes, settings.max_authorization_minor = (
        300,
        2,
        700,
    )
    request = await streaming.request_private_session(
        db_session, payer, profile.id, PrivateSessionMode.one_to_one
    )
    session = await streaming.accept_private_request(db_session, owner, request.id)
    session.status, session.billable_seconds = PrivateSessionStatus.ended, 95
    assert streaming.settlement_amount(session) == 600  # ceil(300 * 95 / 60) < 2-minute minimum
    await streaming.settle_private_session(db_session, session)
    assert session.status is PrivateSessionStatus.settled
    assert await db_session.scalar(
        select(LedgerTransaction).where(
            LedgerTransaction.reference == f"private_session:{session.id}"
        )
    )
    await streaming.settle_private_session(db_session, session)
    assert (
        await db_session.scalars(
            select(LedgerTransaction).where(
                LedgerTransaction.reference == f"private_session:{session.id}"
            )
        )
    ).all().__len__() == 1


@pytest.mark.parametrize(
    ("resolution_type", "expected_session_status", "expected_payment_status"),
    [
        (
            LedgerTransactionType.refund,
            PrivateSessionStatus.cancelled,
            PaymentStatus.refunded,
        ),
        (
            LedgerTransactionType.chargeback,
            PrivateSessionStatus.disputed,
            PaymentStatus.chargeback,
        ),
    ],
)
@pytest.mark.asyncio
async def test_private_provider_reversal_is_exact_terminal_and_idempotent(
    db_session, resolution_type, expected_session_status, expected_payment_status
):
    owner, profile = await creator(db_session, f"reverse-{resolution_type.value}@example.com")
    payer, _ = await accounts.register(
        db_session,
        f"reverse-payer-{resolution_type.value}@example.com",
        "strong-password-123",
        None,
    )
    request = await streaming.request_private_session(
        db_session, payer, profile.id, PrivateSessionMode.one_to_one
    )
    session = await streaming.accept_private_request(db_session, owner, request.id)
    attempt = await db_session.get(PaymentAttempt, session.payment_attempt_id)
    assert attempt is not None
    attempt.status = PaymentStatus.succeeded
    session.status, session.billable_seconds = PrivateSessionStatus.ended, 95
    await streaming.settle_private_session(db_session, session)
    settlement = await db_session.scalar(
        select(PrivateSessionSettlement).where(
            PrivateSessionSettlement.private_session_id == session.id
        )
    )
    assert settlement is not None
    original = await db_session.get(LedgerTransaction, settlement.ledger_transaction_id)
    assert original is not None
    original_entries = (
        await db_session.scalars(
            select(LedgerEntry).where(LedgerEntry.transaction_id == original.id)
        )
    ).all()

    reversed_session = await streaming.reverse_private_session_payment(
        db_session,
        attempt,
        resolution_type=resolution_type,
        reason=f"provider_{resolution_type.value}",
    )
    assert reversed_session is session
    assert session.status is expected_session_status
    assert attempt.status is expected_payment_status
    with pytest.raises(PermissionError, match="unavailable"):
        await streaming.issue_private_token(db_session, payer, session.id)
    assert (
        await streaming.end_private_session(
            db_session, None, session.id, "late_provider_room_finished"
        )
    ).status is expected_session_status

    reversals = (
        await db_session.scalars(
            select(LedgerTransaction).where(
                LedgerTransaction.reversal_of_transaction_id == original.id
            )
        )
    ).all()
    assert len(reversals) == 1
    reversal = reversals[0]
    assert reversal.transaction_type is resolution_type
    reversal_entries = (
        await db_session.scalars(
            select(LedgerEntry).where(LedgerEntry.transaction_id == reversal.id)
        )
    ).all()
    assert sorted(
        (entry.ledger_account_id, entry.direction.value, entry.amount_minor)
        for entry in reversal_entries
    ) == sorted(
        (
            entry.ledger_account_id,
            (
                LedgerDirection.credit.value
                if entry.direction is LedgerDirection.debit
                else LedgerDirection.debit.value
            ),
            entry.amount_minor,
        )
        for entry in original_entries
    )

    await streaming.reverse_private_session_payment(
        db_session,
        attempt,
        resolution_type=resolution_type,
        reason=f"provider_{resolution_type.value}_replay",
    )
    assert (
        len(
            (
                await db_session.scalars(
                    select(LedgerTransaction).where(
                        LedgerTransaction.reversal_of_transaction_id == original.id
                    )
                )
            ).all()
        )
        == 1
    )
    assert (
        len(
            (
                await db_session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.event_type == "private_session.payment_reversed",
                        AuditEvent.target_id == str(session.id),
                    )
                )
            ).all()
        )
        == 1
    )


@pytest.mark.asyncio
async def test_private_provider_reversal_before_settlement_cancels_and_chargeback_dominates(
    db_session,
):
    owner, profile = await creator(db_session, "early-reversal-owner@example.com")
    payer, _ = await accounts.register(
        db_session, "early-reversal-payer@example.com", "strong-password-123", None
    )
    request = await streaming.request_private_session(
        db_session, payer, profile.id, PrivateSessionMode.one_to_one
    )
    session = await streaming.accept_private_request(db_session, owner, request.id)
    attempt = await db_session.get(PaymentAttempt, session.payment_attempt_id)
    assert attempt is not None

    await streaming.reverse_private_session_payment(
        db_session,
        attempt,
        resolution_type=LedgerTransactionType.refund,
        reason="provider_refund_before_authorization",
    )
    assert session.status is PrivateSessionStatus.cancelled
    assert attempt.status is PaymentStatus.refunded
    assert (
        await db_session.scalar(
            select(PrivateSessionSettlement).where(
                PrivateSessionSettlement.private_session_id == session.id
            )
        )
        is None
    )

    await streaming.reverse_private_session_payment(
        db_session,
        attempt,
        resolution_type=LedgerTransactionType.chargeback,
        reason="provider_chargeback_after_refund",
    )
    assert session.status is PrivateSessionStatus.disputed
    assert attempt.status is PaymentStatus.chargeback
    await streaming.reverse_private_session_payment(
        db_session,
        attempt,
        resolution_type=LedgerTransactionType.refund,
        reason="late_refund_must_not_downgrade_chargeback",
    )
    assert session.status is PrivateSessionStatus.disputed
    assert attempt.status is PaymentStatus.chargeback
    assert (
        await streaming.end_private_session(
            db_session, None, session.id, "late_provider_room_finished"
        )
    ).status is PrivateSessionStatus.disputed
    assert await streaming.authorize_private_session(db_session, session) is session
    with pytest.raises(streaming.StreamingError, match="Only ended"):
        await streaming.settle_private_session(db_session, session)
    with pytest.raises(PermissionError, match="unavailable"):
        await streaming.issue_private_token(db_session, payer, session.id)
    assert (
        await db_session.scalar(
            select(LedgerTransaction).where(
                LedgerTransaction.reference == f"private_session_reversal:{session.id}"
            )
        )
        is None
    )


@pytest.mark.asyncio
async def test_signed_private_disputes_deny_access_and_reverse_exact_settlement_once(db_session):
    owner, profile = await creator(db_session, "signed-live-dispute-owner@example.com")
    pending_payer, _ = await accounts.register(
        db_session, "signed-live-pending@example.com", "strong-password-123", None
    )
    pending_request = await streaming.request_private_session(
        db_session, pending_payer, profile.id, PrivateSessionMode.one_to_one
    )
    pending_session = await streaming.accept_private_request(db_session, owner, pending_request.id)
    pending_attempt = await db_session.get(PaymentAttempt, pending_session.payment_attempt_id)
    assert pending_attempt
    pending_dispute, pending_dispute_signature = signed_payment_event(
        pending_attempt,
        "payment.disputed",
        f"private-pending-dispute-{pending_attempt.id}",
    )
    await finance.process_development_webhook(
        db_session, pending_dispute, pending_dispute_signature
    )
    assert pending_attempt.status is PaymentStatus.disputed
    assert pending_session.status is PrivateSessionStatus.disputed
    pending_success, pending_success_signature = signed_payment_event(
        pending_attempt,
        "payment.succeeded",
        f"private-pending-late-success-{pending_attempt.id}",
    )
    await finance.process_development_webhook(
        db_session, pending_success, pending_success_signature
    )
    assert pending_attempt.status is PaymentStatus.disputed
    assert pending_session.status is PrivateSessionStatus.disputed
    with pytest.raises(PermissionError, match="unavailable"):
        await streaming.issue_private_token(db_session, pending_payer, pending_session.id)
    assert (
        await db_session.scalar(
            select(PrivateSessionSettlement.id).where(
                PrivateSessionSettlement.private_session_id == pending_session.id
            )
        )
        is None
    )
    pending_chargeback, pending_chargeback_signature = signed_payment_event(
        pending_attempt,
        "payment.chargeback",
        f"private-pending-chargeback-{pending_attempt.id}",
    )
    await finance.process_development_webhook(
        db_session, pending_chargeback, pending_chargeback_signature
    )
    requirement = await db_session.scalar(
        select(PaymentRefundRequirement).where(
            PaymentRefundRequirement.payment_attempt_id == pending_attempt.id
        )
    )
    assert requirement and requirement.status.value == "completed"
    assert pending_attempt.status is PaymentStatus.chargeback
    assert pending_session.status is PrivateSessionStatus.failed

    settled_payer, _ = await accounts.register(
        db_session, "signed-live-settled@example.com", "strong-password-123", None
    )
    settled_request = await streaming.request_private_session(
        db_session, settled_payer, profile.id, PrivateSessionMode.one_to_one
    )
    settled_session = await streaming.accept_private_request(db_session, owner, settled_request.id)
    settled_attempt = await db_session.get(PaymentAttempt, settled_session.payment_attempt_id)
    assert settled_attempt
    success_payload, success_signature = finance.development_webhook_payload(settled_attempt)
    await finance.process_development_webhook(db_session, success_payload, success_signature)
    assert settled_session.status is PrivateSessionStatus.ready
    settled_session.status = PrivateSessionStatus.ended
    settled_session.billable_seconds = 60
    await streaming.settle_private_session(db_session, settled_session)
    settlement = await db_session.scalar(
        select(PrivateSessionSettlement).where(
            PrivateSessionSettlement.private_session_id == settled_session.id
        )
    )
    assert settlement

    settled_dispute, settled_dispute_signature = signed_payment_event(
        settled_attempt,
        "payment.disputed",
        f"private-settled-dispute-{settled_attempt.id}",
    )
    await finance.process_development_webhook(
        db_session, settled_dispute, settled_dispute_signature
    )
    assert settled_attempt.status is PaymentStatus.disputed
    assert settled_session.status is PrivateSessionStatus.disputed
    assert (
        await db_session.scalar(
            select(LedgerTransaction.id).where(
                LedgerTransaction.reversal_of_transaction_id == settlement.ledger_transaction_id
            )
        )
        is None
    )
    with pytest.raises(PermissionError, match="unavailable"):
        await streaming.issue_private_token(db_session, settled_payer, settled_session.id)

    settled_refund, settled_refund_signature = signed_payment_event(
        settled_attempt,
        "payment.refunded",
        f"private-settled-refund-{settled_attempt.id}",
    )
    await finance.process_development_webhook(db_session, settled_refund, settled_refund_signature)
    assert settled_attempt.status is PaymentStatus.refunded
    assert settled_session.status is PrivateSessionStatus.cancelled
    settled_chargeback, settled_chargeback_signature = signed_payment_event(
        settled_attempt,
        "payment.chargeback",
        f"private-settled-chargeback-{settled_attempt.id}",
    )
    await finance.process_development_webhook(
        db_session, settled_chargeback, settled_chargeback_signature
    )
    assert settled_attempt.status is PaymentStatus.chargeback
    assert settled_session.status is PrivateSessionStatus.disputed
    assert (
        len(
            (
                await db_session.scalars(
                    select(LedgerTransaction).where(
                        LedgerTransaction.reversal_of_transaction_id
                        == settlement.ledger_transaction_id
                    )
                )
            ).all()
        )
        == 1
    )


@pytest.mark.asyncio
async def test_live_report_moderation_access_is_audited_and_public_recording_only(db_session):
    owner, _ = await creator(db_session, "report-owner@example.com")
    viewer, _ = await accounts.register(
        db_session, "report-viewer@example.com", "strong-password-123", None
    )
    room = await streaming.start_live(db_session, owner, "Reportable", LiveAccessMode.public)
    await streaming.join_live(db_session, viewer, room.id)
    chat = await streaming.post_chat(db_session, viewer, room.id, "Please review")
    report = await streaming.report_live(db_session, viewer, room.id, "abuse", "context", chat.id)
    assert isinstance(report, LiveReport)
    context = await streaming.moderator_live_report_context(db_session, owner, report.id, "review")
    assert context["chat"] == {"id": str(chat.id), "body": "Please review"}
    recording = await streaming.request_public_recording(db_session, owner, room.id)
    assert isinstance(recording, LiveRecording)
    assert recording.status is LiveRecordingStatus.requested
