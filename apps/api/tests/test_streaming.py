import json
from base64 import b64encode, urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import new as hmac_new

import pytest
from sqlalchemy import select

from app.accounts import service as accounts
from app.creators import service as creators
from app.integrations.streaming import LiveKitStreamingProvider
from app.models.creator import CreatorStatus
from app.models.finance import LedgerTransaction, PaymentAttempt, PaymentStatus
from app.models.streaming import (
    LiveAccessMode,
    LiveRecording,
    LiveRecordingStatus,
    LiveReport,
    PrivateSession,
    PrivateSessionMode,
    PrivateSessionStatus,
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
        urlsafe_b64encode(hmac_new(b"secret", f"{header}.{payload}".encode(), sha256).digest())
        .rstrip(b"=")
        .decode()
    )
    return f"Bearer {header}.{payload}.{signature}"


def test_livekit_webhook_requires_valid_signature_and_raw_body_hash():
    body = b'{"id":"event-1","event":"participant_joined"}'
    provider = LiveKitStreamingProvider()
    assert provider.verify_webhook(body, signed_livekit_webhook(body))["id"] == "event-1"
    with pytest.raises(ValueError, match="authorization"):
        provider.verify_webhook(body, None)
    with pytest.raises(ValueError, match="authorization"):
        provider.verify_webhook(body, "Bearer invalid")
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
async def test_two_to_one_cannot_bill_or_issue_invitee_token_before_all_participants_join(
    db_session,
):
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
    session.status = PrivateSessionStatus.ready
    with pytest.raises(PermissionError, match="not invited"):
        await streaming.issue_private_token(db_session, stranger, session.id)
    start = datetime(2026, 8, 21, tzinfo=UTC)
    await streaming.private_participant_connected(db_session, owner, session.id, start)
    await streaming.private_participant_connected(db_session, payer, session.id, start)
    assert session.status is PrivateSessionStatus.connecting
    ended = await streaming.end_private_session(
        db_session, owner, session.id, "invitee_absent", start
    )
    assert ended.status is PrivateSessionStatus.cancelled
    assert (
        await db_session.scalar(
            select(LedgerTransaction).where(
                LedgerTransaction.reference == f"private_session:{session.id}"
            )
        )
        is None
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
