import json
from base64 import b64encode, urlsafe_b64decode, urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import new as hmac_new
from io import BytesIO
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from conftest import trusted_self_attested_accounts as _trusted_self_attested_accounts
from sqlalchemy import select

from app.compliance import http as compliance_http
from app.compliance.types import ComplianceAccessError, ComplianceDecision
from app.core.config import get_settings
from app.creators import service as creators
from app.finance import service as finance
from app.integrations import streaming as streaming_integration
from app.integrations.streaming import LiveKitStreamingProvider, StreamingProviderError
from app.models.audit import AuditEvent
from app.models.compliance import AgeAssuranceLevel, ComplianceFeature
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
    LiveChatMessage,
    LiveParticipant,
    LiveRecording,
    LiveRecordingStatus,
    LiveReport,
    LiveRoomStatus,
    PrivateRequestStatus,
    PrivateSession,
    PrivateSessionMode,
    PrivateSessionRequest,
    PrivateSessionSettlement,
    PrivateSessionStatus,
    ProviderLiveEvent,
    SessionParticipant,
)
from app.streaming import service as streaming
from app.streaming.control_outbox import process_due_live_provider_control_intents


class _StreamingAccounts:
    """Legacy streaming fixtures represent PT test users unless stated otherwise."""

    async def register(self, db, email, password, correlation_id, **kwargs):
        kwargs.setdefault("country_code", "PT")
        return await _trusted_self_attested_accounts.register(
            db, email, password, correlation_id, **kwargs
        )

    def __getattr__(self, name):
        return getattr(_trusted_self_attested_accounts, name)


accounts = _StreamingAccounts()


def compliance_decision(
    *, allowed: bool, verification_expires_at: datetime | None = None
) -> ComplianceDecision:
    return ComplianceDecision(
        allowed=allowed,
        code="ALLOWED" if allowed else "AGE_VERIFICATION_REQUIRED",
        action=None if allowed else "VERIFY_AGE",
        reason="Policy allows access" if allowed else "Age verification is required",
        feature=ComplianceFeature.live,
        jurisdiction="PT",
        policy_id=None,
        policy_version=1,
        required_minimum_age=18,
        required_assurance_level=AgeAssuranceLevel.self_attested,
        achieved_assurance_level=(
            AgeAssuranceLevel.self_attested if allowed else AgeAssuranceLevel.none
        ),
        age_access_allowed=allowed,
        feature_allowed=True,
        country_conflict=False,
        verification_expires_at=verification_expires_at,
    )


async def process_committed_live_controls(*, now: datetime | None = None) -> int:
    """Drive the durable worker after the producer transaction commits."""

    result = await process_due_live_provider_control_intents(
        success_hook=streaming.finalize_live_provider_control_success,
        now=now,
    )
    return result.succeeded_count


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


@pytest.mark.asyncio
async def test_livekit_room_controls_use_authenticated_twirp_and_token_exp_is_authority_capped(
    monkeypatch,
):
    requests = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"{}"

    def capture(request, timeout):
        assert timeout == 5
        requests.append(request)
        return Response()

    monkeypatch.setattr(streaming_integration, "_open_livekit_request", capture)
    provider = LiveKitStreamingProvider()
    authority_expiry = datetime.now(UTC) + timedelta(seconds=30)
    token = await provider.participant_token(
        "room-authority",
        "fan-authority",
        can_publish=False,
        can_subscribe=True,
        authority_expires_at=authority_expiry,
    )
    claims = json.loads(
        urlsafe_b64decode(token.split(".")[1] + "=" * (-len(token.split(".")[1]) % 4))
    )
    assert claims["exp"] <= int(authority_expiry.timestamp())
    assert claims["video"]["canPublish"] is False
    assert claims["video"]["canPublishData"] is False
    assert claims["video"]["canUpdateOwnMetadata"] is False

    await provider.remove_participant("room-authority", "fan-authority")
    await provider.close_room("room-authority")
    assert [request.full_url.rsplit("/", 1)[-1] for request in requests] == [
        "RemoveParticipant",
        "DeleteRoom",
    ]
    assert json.loads(requests[0].data) == {
        "room": "room-authority",
        "identity": "fan-authority",
    }
    assert requests[0].headers["Authorization"].startswith("Bearer ")
    remove_service_token = requests[0].headers["Authorization"].removeprefix("Bearer ")
    remove_grant = json.loads(
        urlsafe_b64decode(
            remove_service_token.split(".")[1]
            + "=" * (-len(remove_service_token.split(".")[1]) % 4)
        )
    )["video"]
    assert remove_grant == {"roomAdmin": True, "room": "room-authority"}
    delete_service_token = requests[1].headers["Authorization"].removeprefix("Bearer ")
    delete_grant = json.loads(
        urlsafe_b64decode(
            delete_service_token.split(".")[1]
            + "=" * (-len(delete_service_token.split(".")[1]) % 4)
        )
    )["video"]
    assert delete_grant == {"roomCreate": True}

    def missing_room(_request, timeout):
        assert timeout == 5
        raise HTTPError(
            "https://livekit.invalid/twirp/livekit.RoomService/ListParticipants",
            404,
            "not found",
            {"Content-Type": "application/json"},
            BytesIO(b'{"code":"not_found","msg":"room does not exist"}'),
        )

    monkeypatch.setattr(streaming_integration, "_open_livekit_request", missing_room)
    assert await provider.list_participant_identities("missing-room") == set()

    def proxy_not_found(_request, timeout):
        assert timeout == 5
        raise HTTPError(
            "https://livekit.invalid/twirp/livekit.RoomService/DeleteRoom",
            404,
            "not found",
            {"Content-Type": "text/html"},
            BytesIO(b"<html>proxy route not found</html>"),
        )

    monkeypatch.setattr(streaming_integration, "_open_livekit_request", proxy_not_found)
    with pytest.raises(StreamingProviderError, match="control failed"):
        await provider.close_room("still-connected-room")
    with pytest.raises(StreamingProviderError, match="reconciliation failed"):
        await provider.list_participant_identities("still-connected-room")
    with pytest.raises(StreamingProviderError, match="expired"):
        await provider.participant_token(
            "room-authority",
            "fan-authority",
            can_publish=False,
            can_subscribe=True,
            authority_expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    assert (
        streaming_integration._RejectLiveKitRedirects().redirect_request(
            object(),
            None,
            302,
            "Found",
            {},
            "https://attacker.invalid/collect",
        )
        is None
    )


async def creator(db, email):
    user, _ = await accounts.register(
        db,
        email,
        "strong-password-123",
        None,
        country_code="PT",
    )
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
async def test_live_compliance_denial_precedes_join_token_chat_and_history_mutation(
    db_session, reviewed_pt_compliance_policy
):
    owner, _ = await creator(db_session, "live-compliance-owner@example.com")
    viewer, _ = await accounts.register(
        db_session,
        "live-compliance-viewer@example.com",
        "strong-password-123",
        None,
        country_code="PT",
    )
    denied_viewer, _ = await accounts.register(
        db_session,
        "live-compliance-denied@example.com",
        "strong-password-123",
        None,
        country_code="PT",
    )
    room = await streaming.start_live(
        db_session,
        owner,
        "Protected live",
        LiveAccessMode.public,
        compliance_decision=compliance_decision(allowed=True),
    )
    await streaming.join_live(
        db_session,
        viewer,
        room.id,
        compliance_decision=compliance_decision(allowed=True),
    )
    await db_session.flush()
    initial_viewers = room.viewer_count
    initial_participants = len(
        (
            await db_session.scalars(
                select(LiveParticipant).where(LiveParticipant.live_room_id == room.id)
            )
        ).all()
    )

    with pytest.raises(ComplianceAccessError, match="Age verification"):
        await streaming.join_live(
            db_session,
            denied_viewer,
            room.id,
            compliance_decision=compliance_decision(allowed=False),
        )
    with pytest.raises(ComplianceAccessError, match="Age verification"):
        await streaming.issue_live_token(
            db_session,
            viewer,
            room.id,
            compliance_decision=compliance_decision(allowed=False),
        )
    with pytest.raises(ComplianceAccessError, match="Age verification"):
        await streaming.post_chat(
            db_session,
            viewer,
            room.id,
            "must not persist",
            compliance_decision=compliance_decision(allowed=False),
        )
    with pytest.raises(ComplianceAccessError, match="Age verification"):
        await streaming.live_chat_history(
            db_session,
            viewer,
            room.id,
            compliance_decision=compliance_decision(allowed=False),
        )

    assert room.viewer_count == initial_viewers
    assert (
        len(
            (
                await db_session.scalars(
                    select(LiveParticipant).where(LiveParticipant.live_room_id == room.id)
                )
            ).all()
        )
        == initial_participants
    )
    assert (
        await db_session.scalar(
            select(LiveParticipant.id).where(
                LiveParticipant.live_room_id == room.id,
                LiveParticipant.user_id == denied_viewer.id,
            )
        )
        is None
    )
    assert (
        await db_session.scalar(
            select(LiveChatMessage.id).where(LiveChatMessage.live_room_id == room.id)
        )
        is None
    )


@pytest.mark.asyncio
async def test_creator_compliance_denial_precedes_private_acceptance_and_payment(
    db_session, reviewed_pt_compliance_policy
):
    owner, profile = await creator(db_session, "private-compliance-owner@example.com")
    requester, _ = await accounts.register(
        db_session,
        "private-compliance-requester@example.com",
        "strong-password-123",
        None,
        country_code="PT",
    )
    request = await streaming.request_private_session(
        db_session,
        requester,
        profile.id,
        PrivateSessionMode.one_to_one,
        compliance_decision=compliance_decision(allowed=True),
    )

    with pytest.raises(ComplianceAccessError, match="Age verification"):
        await streaming.accept_private_request(
            db_session,
            owner,
            request.id,
            compliance_decision=compliance_decision(allowed=False),
        )

    assert request.status is PrivateRequestStatus.pending
    assert request.accepted_at is None
    assert (
        await db_session.scalar(
            select(PrivateSession.id).where(PrivateSession.request_id == request.id)
        )
        is None
    )
    assert await db_session.scalar(select(PaymentAttempt.id)) is None


@pytest.mark.asyncio
async def test_private_requests_queue_during_live_but_cannot_be_accepted_until_live_ends(
    db_session, livekit_control
):
    owner, profile = await creator(db_session, "stream-owner@example.com")
    viewer, _ = await accounts.register(
        db_session,
        "stream-viewer@example.com",
        "strong-password-123",
        None,
        country_code="PT",
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
    assert room.status is LiveRoomStatus.ending
    assert livekit_control.closed_rooms == []
    await db_session.commit()
    assert await process_committed_live_controls() == 1
    await db_session.refresh(room)
    assert livekit_control.closed_rooms == [room.provider_room_name]
    session = await streaming.accept_private_request(db_session, owner, queued.id)
    assert session.status is PrivateSessionStatus.awaiting_payment_authorization
    assert session.payment_attempt_id is not None
    assert session.provider_room_name


@pytest.mark.asyncio
async def test_public_private_exclusivity_includes_pending_provider_termination(
    db_session,
    livekit_control,
    monkeypatch,
):
    owner, profile = await creator(db_session, "exclusive-live-owner@example.com")
    first_payer, _ = await accounts.register(
        db_session,
        "exclusive-live-first@example.com",
        "strong-password-123",
        None,
        country_code="PT",
    )
    second_payer, _ = await accounts.register(
        db_session,
        "exclusive-live-second@example.com",
        "strong-password-123",
        None,
        country_code="PT",
    )
    first_request = await streaming.request_private_session(
        db_session,
        first_payer,
        profile.id,
        PrivateSessionMode.one_to_one,
    )
    second_request = await streaming.request_private_session(
        db_session,
        second_payer,
        profile.id,
        PrivateSessionMode.one_to_one,
    )
    first_session = await streaming.accept_private_request(
        db_session,
        owner,
        first_request.id,
    )
    first_session.status = PrivateSessionStatus.ready

    with pytest.raises(streaming.StreamingError, match="private live"):
        await streaming.start_live(
            db_session,
            owner,
            "Must wait for private delivery",
            LiveAccessMode.public,
        )

    class FailingControl:
        async def close_room(self, _room_name):
            raise StreamingProviderError("provider unavailable")

        async def remove_participant(self, _room_name, _identity):
            raise StreamingProviderError("provider unavailable")

    monkeypatch.setattr(streaming, "livekit_control_provider", FailingControl)
    ending = await streaming.end_private_session(
        db_session,
        owner,
        first_session.id,
        "ended_by_creator",
    )
    assert ending.status is PrivateSessionStatus.ending
    with pytest.raises(streaming.StreamingError, match="active private"):
        await streaming.accept_private_request(db_session, owner, second_request.id)

    monkeypatch.setattr(streaming, "livekit_control_provider", lambda: livekit_control)
    ended = await streaming.end_private_session(
        db_session,
        None,
        first_session.id,
        "retry",
        provider_room_closed=True,
    )
    assert ended.status is PrivateSessionStatus.cancelled
    replacement = await streaming.accept_private_request(
        db_session,
        owner,
        second_request.id,
    )
    assert replacement.id != first_session.id


@pytest.mark.asyncio
async def test_live_control_failure_preserves_active_state_and_ban_disconnects_provider(
    db_session,
    livekit_control,
    monkeypatch,
):
    owner, _ = await creator(db_session, "control-owner@example.com")
    viewer, _ = await accounts.register(
        db_session,
        "control-viewer@example.com",
        "strong-password-123",
        None,
    )
    room = await streaming.start_live(
        db_session,
        owner,
        "Controlled live",
        LiveAccessMode.public,
    )
    await streaming.join_live(db_session, viewer, room.id)
    ban = await streaming.ban_live_viewer(
        db_session,
        owner,
        room.id,
        viewer.id,
        "abusive chat",
    )
    assert ban.user_id == viewer.id
    assert livekit_control.removed_participants == []
    await db_session.commit()
    assert await process_committed_live_controls() == 1
    await db_session.refresh(room)
    participant = await db_session.scalar(
        select(LiveParticipant).where(
            LiveParticipant.live_room_id == room.id,
            LiveParticipant.user_id == viewer.id,
        )
    )
    assert participant is not None and participant.left_at is not None
    assert livekit_control.removed_participants == [(room.provider_room_name, str(viewer.id))]
    await streaming.process_livekit_webhook(
        db_session,
        {
            "id": "banned-viewer-cached-token-rejoin",
            "event": "participant_joined",
            "room": {"name": room.provider_room_name},
            "participant": {"identity": str(viewer.id)},
        },
    )
    await db_session.commit()
    assert await process_committed_live_controls() == 1
    assert livekit_control.removed_participants == [
        (room.provider_room_name, str(viewer.id)),
        (room.provider_room_name, str(viewer.id)),
    ]

    class FailingControl:
        async def close_room(self, _room_name):
            raise StreamingProviderError("provider unavailable")

        async def remove_participant(self, _room_name, _identity):
            raise StreamingProviderError("provider unavailable")

    monkeypatch.setattr(streaming, "livekit_control_provider", FailingControl)
    pending_end = await streaming.end_live(db_session, owner, room.id)
    assert pending_end.status.value == "ending"
    assert pending_end.ended_at is None
    await db_session.commit()
    assert await process_committed_live_controls() == 0
    assert await db_session.scalar(
        select(AuditEvent.id).where(
            AuditEvent.event_type == "streaming.provider_control_retry_scheduled",
        )
    )
    monkeypatch.setattr(streaming, "livekit_control_provider", lambda: livekit_control)
    await streaming.process_livekit_webhook(
        db_session,
        {
            "id": "pending-end-provider-finished",
            "event": "room_finished",
            "room": {"name": room.provider_room_name},
        },
    )
    assert room.status.value == "ended"
    assert livekit_control.closed_rooms == []
    await db_session.commit()

    retry_room = await streaming.start_live(
        db_session,
        owner,
        "Retryable viewer control",
        LiveAccessMode.public,
    )

    retry_viewer, _ = await accounts.register(
        db_session,
        "control-retry-viewer@example.com",
        "strong-password-123",
        None,
    )
    retry_participant = await streaming.join_live(db_session, retry_viewer, retry_room.id)
    monkeypatch.setattr(streaming, "livekit_control_provider", FailingControl)
    retry_ban = await streaming.ban_live_viewer(
        db_session,
        owner,
        retry_room.id,
        retry_viewer.id,
        "provider retry required",
    )
    assert retry_ban.user_id == retry_viewer.id
    assert retry_participant.left_at is None
    await db_session.commit()
    assert await process_committed_live_controls() == 0
    assert await db_session.scalar(
        select(AuditEvent.id).where(
            AuditEvent.event_type == "streaming.provider_control_retry_scheduled",
        )
    )
    monkeypatch.setattr(
        streaming,
        "livekit_control_provider",
        lambda: livekit_control,
    )
    assert await process_committed_live_controls(now=datetime.now(UTC) + timedelta(seconds=6)) >= 1
    await db_session.refresh(retry_participant)
    assert retry_participant.left_at is not None
    assert (retry_room.provider_room_name, str(retry_viewer.id)) in (
        livekit_control.removed_participants
    )


@pytest.mark.asyncio
async def test_cached_token_cannot_recreate_ended_public_room(
    db_session,
    livekit_control,
):
    owner, _ = await creator(db_session, "ended-room-owner@example.com")
    room = await streaming.start_live(
        db_session,
        owner,
        "Ended provider room",
        LiveAccessMode.public,
    )
    await streaming.end_live(db_session, owner, room.id)
    await streaming.process_livekit_webhook(
        db_session,
        {
            "id": "ended-room-cached-token-rejoin",
            "event": "participant_joined",
            "room": {"name": room.provider_room_name},
            "participant": {"identity": str(owner.id)},
        },
    )
    await db_session.commit()
    # The initial committed end intent owns the close command. A cached join
    # cannot replace it with a competing duplicate control request.
    assert await process_committed_live_controls() == 1
    assert livekit_control.closed_rooms == [room.provider_room_name]
    await db_session.refresh(room)
    assert room.status.value == "ended"


@pytest.mark.asyncio
async def test_signed_cached_join_commits_durable_eviction_before_provider_retry(
    db_session,
    livekit_control,
    monkeypatch,
):
    owner, _ = await creator(db_session, "callback-retry-owner@example.com")
    room = await streaming.start_live(
        db_session,
        owner,
        "Callback retry",
        LiveAccessMode.public,
    )
    await streaming.end_live(db_session, owner, room.id)
    room_name = room.provider_room_name
    await db_session.commit()

    class FailingControl:
        async def close_room(self, _room_name):
            raise StreamingProviderError("provider unavailable")

        async def remove_participant(self, _room_name, _identity):
            raise StreamingProviderError("provider unavailable")

    event = {
        "id": "cached-join-provider-control-retry",
        "event": "participant_joined",
        "room": {"name": room_name},
        "participant": {"identity": str(owner.id)},
    }
    monkeypatch.setattr(streaming, "livekit_control_provider", FailingControl)
    await streaming.process_livekit_webhook(db_session, event)
    await db_session.commit()
    assert await process_committed_live_controls() == 0
    assert await db_session.scalar(
        select(ProviderLiveEvent.id).where(ProviderLiveEvent.external_event_id == event["id"])
    )

    monkeypatch.setattr(streaming, "livekit_control_provider", lambda: livekit_control)
    assert await process_committed_live_controls(now=datetime.now(UTC) + timedelta(seconds=6)) >= 1
    assert room_name in livekit_control.closed_rooms


@pytest.mark.asyncio
async def test_failed_moderation_delete_is_durable_and_retried(
    db_session,
    livekit_control,
    monkeypatch,
):
    owner, _ = await creator(db_session, "moderation-retry-owner@example.com")
    moderator, _ = await accounts.register(
        db_session,
        "moderation-retry-actor@example.com",
        "strong-password-123",
        None,
    )
    room = await streaming.start_live(
        db_session,
        owner,
        "Moderation retry",
        LiveAccessMode.public,
    )

    class FailingControl:
        async def close_room(self, _room_name):
            raise StreamingProviderError("provider unavailable")

        async def remove_participant(self, _room_name, _identity):
            raise StreamingProviderError("provider unavailable")

    monkeypatch.setattr(streaming, "livekit_control_provider", FailingControl)
    pending = await streaming.terminate_live_for_moderation(
        db_session,
        moderator,
        room.id,
        "urgent containment",
    )
    assert pending.status is LiveRoomStatus.ending
    assert await db_session.scalar(
        select(AuditEvent.id).where(
            AuditEvent.event_type == "live.termination_enqueued",
            AuditEvent.target_id == str(room.id),
        )
    )
    await db_session.commit()
    assert await process_committed_live_controls() == 0

    monkeypatch.setattr(streaming, "livekit_control_provider", lambda: livekit_control)
    assert await process_committed_live_controls(now=datetime.now(UTC) + timedelta(seconds=6)) == 1
    await db_session.refresh(room)
    assert room.status is LiveRoomStatus.ended
    assert livekit_control.closed_rooms == [room.provider_room_name]


@pytest.mark.asyncio
async def test_pending_public_termination_keeps_its_original_durable_command(db_session):
    owner, _ = await creator(db_session, "pending-public-command-owner@example.com")
    room = await streaming.start_live(db_session, owner, "Pending authority close", LiveAccessMode.public)

    assert await streaming._enqueue_public_room_termination(
        db_session, room, reason="initial_authority_denial"
    )
    # A later policy sweep carries a different diagnostic reason.  It must
    # retain the committed command rather than collide on its idempotency key.
    assert not await streaming._enqueue_public_room_termination(
        db_session, room, reason="later_policy_denial"
    )


@pytest.mark.asyncio
async def test_creator_suspension_immediately_deletes_active_provider_room(
    db_session,
    livekit_control,
):
    owner, profile = await creator(db_session, "suspended-live-owner@example.com")
    room = await streaming.start_live(
        db_session,
        owner,
        "Suspension boundary",
        LiveAccessMode.public,
    )
    await creators.set_status(
        db_session,
        profile,
        CreatorStatus.suspended,
        owner.id,
        "urgent moderation containment",
    )
    assert livekit_control.closed_rooms == []
    await db_session.commit()
    assert await process_committed_live_controls() == 1
    assert livekit_control.closed_rooms == [room.provider_room_name]
    await db_session.refresh(room)
    assert room.status.value == "ended"
    assert profile.is_public is False


@pytest.mark.asyncio
async def test_failed_creator_suspension_control_still_denies_fresh_public_and_private_tokens(
    db_session,
    monkeypatch,
):
    class FailingControl:
        async def close_room(self, _room_name):
            raise StreamingProviderError("provider unavailable")

        async def remove_participant(self, _room_name, _identity):
            raise StreamingProviderError("provider unavailable")

    monkeypatch.setattr(streaming, "livekit_control_provider", FailingControl)

    public_owner, public_profile = await creator(
        db_session,
        "failed-suspension-public@example.com",
    )
    room = await streaming.start_live(
        db_session,
        public_owner,
        "Suspension outage",
        LiveAccessMode.public,
    )
    await creators.set_status(
        db_session,
        public_profile,
        CreatorStatus.suspended,
        public_owner.id,
        "urgent suspension during provider outage",
    )
    assert room.status is LiveRoomStatus.ending
    with pytest.raises(PermissionError, match="unavailable"):
        await streaming.issue_live_token(db_session, public_owner, room.id)

    private_owner, private_profile = await creator(
        db_session,
        "failed-suspension-private@example.com",
    )
    payer, _ = await accounts.register(
        db_session,
        "failed-suspension-payer@example.com",
        "strong-password-123",
        None,
    )
    request = await streaming.request_private_session(
        db_session,
        payer,
        private_profile.id,
        PrivateSessionMode.one_to_one,
    )
    session = await streaming.accept_private_request(
        db_session,
        private_owner,
        request.id,
    )
    session.status = PrivateSessionStatus.ready
    await creators.set_status(
        db_session,
        private_profile,
        CreatorStatus.suspended,
        private_owner.id,
        "urgent private suspension during provider outage",
    )
    assert session.status is PrivateSessionStatus.ending
    with pytest.raises(PermissionError, match="unavailable"):
        await streaming.issue_private_token(db_session, payer, session.id)


@pytest.mark.asyncio
async def test_live_token_is_capped_by_current_compliance_authority(db_session):
    owner, _ = await creator(db_session, "token-cap-owner@example.com")
    viewer, _ = await accounts.register(
        db_session,
        "token-cap-viewer@example.com",
        "strong-password-123",
        None,
    )
    room = await streaming.start_live(
        db_session,
        owner,
        "Authority-capped live",
        LiveAccessMode.public,
    )
    authority_expiry = datetime.now(UTC) + timedelta(seconds=45)
    _, token = await streaming.issue_live_token(
        db_session,
        viewer,
        room.id,
        compliance_decision=compliance_decision(
            allowed=True,
            verification_expires_at=authority_expiry,
        ),
    )
    encoded = token.split(".")[1]
    claims = json.loads(urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    assert claims["exp"] <= int(authority_expiry.timestamp())


@pytest.mark.asyncio
async def test_optional_creator_verification_expiry_does_not_poison_live_token(db_session):
    owner, profile = await creator(db_session, "optional-expiry-owner@example.com")
    room = await streaming.start_live(
        db_session,
        owner,
        "Optional creator evidence",
        LiveAccessMode.public,
    )
    verification = await creators.latest_verification(db_session, profile.id)
    assert verification is not None
    verification.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    _, token = await streaming.issue_live_token(
        db_session,
        owner,
        room.id,
        compliance_decision=compliance_decision(allowed=True),
    )
    payload = token.split(".")[1]
    claims = json.loads(urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert claims["exp"] > int(datetime.now(UTC).timestamp())


@pytest.mark.asyncio
async def test_private_token_expiry_is_bounded_by_every_required_participant(
    db_session,
    monkeypatch,
):
    owner, profile = await creator(db_session, "participant-cap-owner@example.com")
    payer, _ = await accounts.register(
        db_session,
        "participant-cap-payer@example.com",
        "strong-password-123",
        None,
    )
    invited, _ = await accounts.register(
        db_session,
        "participant-cap-invited@example.com",
        "strong-password-123",
        None,
    )
    request = await streaming.request_private_session(
        db_session,
        payer,
        profile.id,
        PrivateSessionMode.two_to_one,
        invited.id,
    )
    session = await streaming.accept_private_request(db_session, owner, request.id)
    session.status = PrivateSessionStatus.ready
    invitee_expiry = datetime.now(UTC) + timedelta(seconds=30)

    async def participant_decision(_db, user, decision=None):
        if decision is not None:
            return decision
        return compliance_decision(
            allowed=True,
            verification_expires_at=(
                invitee_expiry
                if user.id == invited.id
                else datetime.now(UTC) + timedelta(minutes=5)
            ),
        )

    monkeypatch.setattr(streaming, "require_live_compliance", participant_decision)
    _, token = await streaming.issue_private_token(
        db_session,
        payer,
        session.id,
        compliance_decision=compliance_decision(
            allowed=True,
            verification_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        ),
    )
    payload = token.split(".")[1]
    claims = json.loads(urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    assert claims["exp"] <= int(invitee_expiry.timestamp())


@pytest.mark.asyncio
async def test_compliance_revocation_evicts_connected_viewer_and_reconciliation_is_idempotent(
    db_session,
    livekit_control,
):
    owner, _ = await creator(db_session, "eviction-owner@example.com")
    viewer, _ = await accounts.register(
        db_session,
        "eviction-viewer@example.com",
        "strong-password-123",
        None,
    )
    room = await streaming.start_live(
        db_session,
        owner,
        "Revocation live",
        LiveAccessMode.public,
    )
    participant = await streaming.join_live(db_session, viewer, room.id)
    assert participant.left_at is None and room.viewer_count == 1

    viewer.adult_attested_at = None
    viewer.adult_attestation_version = None
    await db_session.flush()
    assert (
        await streaming.evict_user_from_active_live(
            db_session,
            viewer.id,
            reason="test_verification_revoked",
        )
        == 1
    )
    assert participant.left_at is None and room.viewer_count == 1
    await db_session.commit()
    assert await process_committed_live_controls() == 1
    await db_session.refresh(participant)
    await db_session.refresh(room)
    assert participant.left_at is not None and room.viewer_count == 0
    assert livekit_control.removed_participants == [(room.provider_room_name, str(viewer.id))]
    assert await streaming.reconcile_live_compliance_authority(db_session) == 0


@pytest.mark.asyncio
async def test_effective_legal_reacceptance_is_composed_into_connected_live_authority(
    db_session,
    livekit_control,
    monkeypatch,
):
    owner, _ = await creator(db_session, "legal-live-owner@example.com")
    viewer, _ = await accounts.register(
        db_session,
        "legal-live-viewer@example.com",
        "strong-password-123",
        None,
    )
    room = await streaming.start_live(
        db_session,
        owner,
        "New legal version",
        LiveAccessMode.public,
    )
    await streaming.join_live(db_session, viewer, room.id)

    async def has_requirements(*_args, **_kwargs):
        return True

    async def required_documents(*_args, **_kwargs):
        return [SimpleNamespace(version_id="legal-v2")]

    monkeypatch.setattr(
        compliance_http.legal_service,
        "has_effective_acceptance_requirements",
        has_requirements,
    )
    monkeypatch.setattr(
        compliance_http.legal_service,
        "required_documents",
        required_documents,
    )
    assert await streaming.reconcile_live_compliance_authority(db_session, limit=1) == 1
    assert room.status is LiveRoomStatus.ending
    assert livekit_control.closed_rooms == []
    assert await process_committed_live_controls() == 1
    await db_session.refresh(room)
    assert room.status is LiveRoomStatus.ended
    assert livekit_control.closed_rooms == [room.provider_room_name]


@pytest.mark.asyncio
async def test_live_reconciliation_keyset_pages_reach_later_public_and_private_rows(
    db_session,
    livekit_control,
):
    public_rows = []
    for index in range(2):
        owner, profile = await creator(
            db_session,
            f"paged-public-owner-{index}@example.com",
        )
        room = await streaming.start_live(
            db_session,
            owner,
            f"Paged public {index}",
            LiveAccessMode.public,
        )
        public_rows.append((room, profile))
    later_room, later_profile = max(public_rows, key=lambda item: item[0].id)
    earlier_room = min(public_rows, key=lambda item: item[0].id)[0]
    later_profile.is_public = False

    private_rows = []
    for index in range(2):
        owner, profile = await creator(
            db_session,
            f"paged-private-owner-{index}@example.com",
        )
        payer, _ = await accounts.register(
            db_session,
            f"paged-private-payer-{index}@example.com",
            "strong-password-123",
            None,
        )
        request = await streaming.request_private_session(
            db_session,
            payer,
            profile.id,
            PrivateSessionMode.one_to_one,
        )
        session = await streaming.accept_private_request(db_session, owner, request.id)
        session.status = PrivateSessionStatus.ready
        private_rows.append((session, payer))
    later_session, later_payer = max(private_rows, key=lambda item: item[0].id)
    earlier_session = min(private_rows, key=lambda item: item[0].id)[0]
    later_payer.adult_attested_at = None
    later_payer.adult_attestation_version = None

    assert await streaming.reconcile_live_compliance_authority(db_session, limit=1) == 2
    assert later_room.status is LiveRoomStatus.ending
    assert later_session.status is PrivateSessionStatus.ending
    assert await process_committed_live_controls() == 2
    await db_session.refresh(later_room)
    await db_session.refresh(earlier_room)
    await db_session.refresh(later_session)
    await db_session.refresh(earlier_session)
    assert later_room.status is LiveRoomStatus.ended
    assert earlier_room.status is LiveRoomStatus.live
    assert later_session.status is PrivateSessionStatus.cancelled
    assert earlier_session.status is PrivateSessionStatus.ready
    assert later_room.provider_room_name in livekit_control.closed_rooms
    assert later_session.provider_room_name in livekit_control.closed_rooms


@pytest.mark.asyncio
async def test_private_cached_join_rechecks_all_current_authority_before_billing(
    db_session,
    livekit_control,
):
    owner, profile = await creator(db_session, "private-rejoin-owner@example.com")
    payer, _ = await accounts.register(
        db_session,
        "private-rejoin-payer@example.com",
        "strong-password-123",
        None,
    )
    request = await streaming.request_private_session(
        db_session,
        payer,
        profile.id,
        PrivateSessionMode.one_to_one,
    )
    session = await streaming.accept_private_request(db_session, owner, request.id)
    session.status = PrivateSessionStatus.ready
    payer.adult_attested_at = None
    payer.adult_attestation_version = None
    await db_session.flush()

    await streaming.process_livekit_webhook(
        db_session,
        {
            "id": "private-cached-token-denied",
            "event": "participant_joined",
            "createdAt": str(int(datetime.now(UTC).timestamp())),
            "room": {"name": session.provider_room_name},
            "participant": {"identity": str(payer.id)},
        },
    )
    assert session.status is PrivateSessionStatus.ending
    assert livekit_control.closed_rooms == []
    await db_session.commit()
    assert await process_committed_live_controls() == 1
    await db_session.refresh(session)
    assert session.status is PrivateSessionStatus.cancelled
    assert session.billable_seconds == 0
    assert livekit_control.closed_rooms == [session.provider_room_name]
    participant = await db_session.scalar(
        select(SessionParticipant).where(
            SessionParticipant.private_session_id == session.id,
            SessionParticipant.user_id == payer.id,
        )
    )
    assert participant is not None and participant.joined_at is None
    assert await db_session.scalar(
        select(ProviderLiveEvent.id).where(
            ProviderLiveEvent.external_event_id == "private-cached-token-denied"
        )
    )


@pytest.mark.asyncio
async def test_private_denied_cached_join_retries_when_delete_room_fails(
    db_session,
    livekit_control,
    monkeypatch,
):
    owner, profile = await creator(db_session, "private-retry-owner@example.com")
    payer, _ = await accounts.register(
        db_session,
        "private-retry-payer@example.com",
        "strong-password-123",
        None,
    )
    request = await streaming.request_private_session(
        db_session,
        payer,
        profile.id,
        PrivateSessionMode.one_to_one,
    )
    session = await streaming.accept_private_request(db_session, owner, request.id)
    session.status = PrivateSessionStatus.ready
    payer.adult_attested_at = None
    payer.adult_attestation_version = None
    event = {
        "id": "private-denied-control-retry",
        "event": "participant_joined",
        "createdAt": str(int(datetime.now(UTC).timestamp())),
        "room": {"name": session.provider_room_name},
        "participant": {"identity": str(payer.id)},
    }
    await db_session.commit()

    class FailingControl:
        async def close_room(self, _room_name):
            raise StreamingProviderError("provider unavailable")

        async def remove_participant(self, _room_name, _identity):
            raise StreamingProviderError("provider unavailable")

    monkeypatch.setattr(streaming, "livekit_control_provider", FailingControl)
    pending = await streaming.process_livekit_webhook(db_session, event)
    assert pending is not None and pending.status is PrivateSessionStatus.ending
    await db_session.commit()
    assert await process_committed_live_controls() == 0
    assert await db_session.scalar(
        select(ProviderLiveEvent.id).where(ProviderLiveEvent.external_event_id == event["id"])
    )

    monkeypatch.setattr(streaming, "livekit_control_provider", lambda: livekit_control)
    assert await process_committed_live_controls(now=datetime.now(UTC) + timedelta(seconds=6)) == 1
    await db_session.refresh(session)
    assert session.status is PrivateSessionStatus.cancelled
    assert livekit_control.closed_rooms == [session.provider_room_name]


@pytest.mark.asyncio
async def test_public_terminal_paths_close_durable_chat_membership(
    db_session,
):
    owner, _ = await creator(db_session, "terminal-chat-owner@example.com")
    viewer, _ = await accounts.register(
        db_session,
        "terminal-chat-viewer@example.com",
        "strong-password-123",
        None,
    )
    room = await streaming.start_live(
        db_session,
        owner,
        "Terminal chat",
        LiveAccessMode.public,
    )
    await streaming.join_live(db_session, viewer, room.id)
    await streaming.post_chat(db_session, viewer, room.id, "before end")
    await streaming.end_live(db_session, owner, room.id)
    with pytest.raises(PermissionError, match="active room membership"):
        await streaming.post_chat(db_session, viewer, room.id, "after end")
    with pytest.raises(PermissionError, match="active room membership"):
        await streaming.live_chat_history(db_session, viewer, room.id)
    await db_session.commit()
    assert await process_committed_live_controls() == 1

    second = await streaming.start_live(
        db_session,
        owner,
        "Provider terminal chat",
        LiveAccessMode.public,
    )
    await streaming.join_live(db_session, viewer, second.id)
    await streaming.process_livekit_webhook(
        db_session,
        {
            "id": "provider-room-finished-chat",
            "event": "room_finished",
            "room": {"name": second.provider_room_name},
        },
    )
    assert second.status is LiveRoomStatus.ended
    with pytest.raises(PermissionError, match="active room membership"):
        await streaming.live_chat_history(db_session, viewer, second.id)


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
        db_session,
        "presence-payer@example.com",
        "strong-password-123",
        None,
        country_code="PT",
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
    active_started_at = session.active_started_at
    await streaming.process_livekit_webhook(
        db_session,
        {
            "id": "delayed-signed-join-after-presence-repair",
            "event": "participant_joined",
            "createdAt": str(int(session.accepted_at.timestamp())),
            "room": {"name": session.provider_room_name},
            "participant": {"identity": str(payer.id)},
        },
    )
    assert session.status is PrivateSessionStatus.active
    assert session.active_started_at == active_started_at
    assert await db_session.scalar(
        select(ProviderLiveEvent.id).where(
            ProviderLiveEvent.external_event_id == "delayed-signed-join-after-presence-repair"
        )
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
        db_session,
        "presence-ready-payer@example.com",
        "strong-password-123",
        None,
        country_code="PT",
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
async def test_private_presence_reconciliation_rotates_a_bounded_persisted_cursor(
    db_session,
    monkeypatch,
):
    sessions = []
    for suffix in ("a", "b"):
        owner, profile = await creator(
            db_session,
            f"presence-rotation-owner-{suffix}@example.com",
        )
        payer, _ = await accounts.register(
            db_session,
            f"presence-rotation-payer-{suffix}@example.com",
            "strong-password-123",
            None,
        )
        request = await streaming.request_private_session(
            db_session,
            payer,
            profile.id,
            PrivateSessionMode.one_to_one,
        )
        session = await streaming.accept_private_request(db_session, owner, request.id)
        session.status = PrivateSessionStatus.ready
        sessions.append(session)
    await db_session.commit()

    class Provider:
        def __init__(self):
            self.rooms = []

        async def list_participant_identities(self, room_name: str) -> set[str]:
            self.rooms.append(room_name)
            return set()

    provider = Provider()
    monkeypatch.setattr(streaming, "LiveKitStreamingProvider", lambda: provider)
    first_check = datetime.now(UTC).replace(microsecond=0)
    assert (
        await streaming.reconcile_private_provider_presence(
            db_session,
            limit=1,
            now=first_check,
        )
        == 0
    )
    assert (
        await streaming.reconcile_private_provider_presence(
            db_session,
            limit=1,
            now=first_check + timedelta(seconds=10),
        )
        == 0
    )
    assert len(provider.rooms) == 2
    assert set(provider.rooms) == {session.provider_room_name for session in sessions}


@pytest.mark.asyncio
async def test_reconnect_expiry_is_bounded_and_advances_after_provider_failure(
    db_session,
    monkeypatch,
):
    sessions = []
    cutoff = datetime.now(UTC).replace(microsecond=0) - timedelta(minutes=5)
    for suffix in ("a", "b"):
        owner, profile = await creator(
            db_session,
            f"reconnect-rotation-owner-{suffix}@example.com",
        )
        payer, _ = await accounts.register(
            db_session,
            f"reconnect-rotation-payer-{suffix}@example.com",
            "strong-password-123",
            None,
        )
        request = await streaming.request_private_session(
            db_session,
            payer,
            profile.id,
            PrivateSessionMode.one_to_one,
        )
        session = await streaming.accept_private_request(db_session, owner, request.id)
        session.status = PrivateSessionStatus.reconnecting
        session.disconnected_at = cutoff
        sessions.append(session)
    await db_session.commit()

    class FailingControl:
        async def close_room(self, _room_name):
            raise StreamingProviderError("provider unavailable")

        async def remove_participant(self, _room_name, _identity):
            raise StreamingProviderError("provider unavailable")

    monkeypatch.setattr(streaming, "livekit_control_provider", FailingControl)
    current = cutoff + timedelta(minutes=5)
    assert await streaming.expire_reconnect_grace(db_session, current, limit=1) == 1
    assert sum(session.status is PrivateSessionStatus.ending for session in sessions) == 1
    assert await streaming.expire_reconnect_grace(db_session, current, limit=1) == 1
    assert all(session.status is PrivateSessionStatus.ending for session in sessions)


@pytest.mark.asyncio
async def test_two_to_one_snapshots_separate_rate_and_specific_invitee(db_session):
    owner, profile = await creator(db_session, "two-owner@example.com")
    payer, _ = await accounts.register(
        db_session, "two-payer@example.com", "strong-password-123", None, country_code="PT"
    )
    invited, _ = await accounts.register(
        db_session, "two-invited@example.com", "strong-password-123", None, country_code="PT"
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
async def test_two_to_one_real_lifecycle_has_one_payer_timer_and_settlement(
    db_session, livekit_control
):
    owner, profile = await creator(db_session, "waiting-owner@example.com")
    payer, _ = await accounts.register(
        db_session, "waiting-payer@example.com", "strong-password-123", None, country_code="PT"
    )
    invited, _ = await accounts.register(
        db_session, "waiting-invited@example.com", "strong-password-123", None, country_code="PT"
    )
    stranger, _ = await accounts.register(
        db_session, "waiting-stranger@example.com", "strong-password-123", None, country_code="PT"
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
    assert settled.status is PrivateSessionStatus.ending
    assert livekit_control.closed_rooms == []
    await db_session.commit()
    assert await process_committed_live_controls() == 1
    await db_session.refresh(session)
    assert livekit_control.closed_rooms == [session.provider_room_name]
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
        db_session, "reconnect-payer@example.com", "strong-password-123", None, country_code="PT"
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
        db_session,
        "authorization-payer@example.com",
        "strong-password-123",
        None,
        country_code="PT",
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
        db_session, "event-payer@example.com", "strong-password-123", None, country_code="PT"
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
async def test_signed_private_events_use_provider_occurrence_and_ignore_stale_or_aborted_time(
    db_session,
):
    owner, profile = await creator(db_session, "occurred-owner@example.com")
    payer, _ = await accounts.register(
        db_session,
        "occurred-payer@example.com",
        "strong-password-123",
        None,
    )
    request = await streaming.request_private_session(
        db_session,
        payer,
        profile.id,
        PrivateSessionMode.one_to_one,
    )
    session = await streaming.accept_private_request(db_session, owner, request.id)
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=30)
    session.status = PrivateSessionStatus.ready
    session.accepted_at = base - timedelta(seconds=1)
    session.ready_at = base - timedelta(seconds=1)

    async def provider_event(event_id, event_type, user_id, occurred_at):
        return await streaming.process_livekit_webhook(
            db_session,
            {
                "id": event_id,
                "event": event_type,
                "createdAt": str(int(occurred_at.timestamp())),
                "room": {"name": session.provider_room_name},
                "participant": {"identity": str(user_id)},
            },
        )

    await provider_event("occurred-owner-join", "participant_joined", owner.id, base)
    await provider_event("occurred-payer-join", "participant_joined", payer.id, base)
    await provider_event(
        "occurred-payer-leave",
        "participant_left",
        payer.id,
        base + timedelta(seconds=15),
    )
    assert session.billable_seconds == 15
    assert session.status is PrivateSessionStatus.reconnecting

    # A distinct but older signed event is persisted/deduped without rewinding
    # the participant or restarting the billable interval.
    await provider_event(
        "occurred-payer-stale-join",
        "participant_joined",
        payer.id,
        base + timedelta(seconds=10),
    )
    assert session.billable_seconds == 15
    assert session.status is PrivateSessionStatus.reconnecting

    aborted_owner, aborted_profile = await creator(
        db_session,
        "aborted-owner@example.com",
    )
    aborted_payer, _ = await accounts.register(
        db_session,
        "aborted-payer@example.com",
        "strong-password-123",
        None,
    )
    aborted_request = await streaming.request_private_session(
        db_session,
        aborted_payer,
        aborted_profile.id,
        PrivateSessionMode.one_to_one,
    )
    aborted_session = await streaming.accept_private_request(
        db_session,
        aborted_owner,
        aborted_request.id,
    )
    aborted_session.status = PrivateSessionStatus.ready
    aborted_session.accepted_at = base - timedelta(seconds=1)
    aborted_session.ready_at = base - timedelta(seconds=1)
    await streaming.process_livekit_webhook(
        db_session,
        {
            "id": "aborted-before-media-active",
            "event": "participant_connection_aborted",
            "createdAt": str(int(base.timestamp())),
            "room": {"name": aborted_session.provider_room_name},
            "participant": {"identity": str(aborted_payer.id)},
        },
    )
    aborted_participant = await db_session.scalar(
        select(SessionParticipant).where(
            SessionParticipant.private_session_id == aborted_session.id,
            SessionParticipant.user_id == aborted_payer.id,
        )
    )
    assert aborted_session.status is PrivateSessionStatus.ready
    assert aborted_session.billable_seconds == 0
    assert aborted_participant is not None
    assert aborted_participant.joined_at is None and aborted_participant.left_at is None


@pytest.mark.asyncio
async def test_private_room_finished_uses_signed_occurrence_for_billing(db_session):
    owner, profile = await creator(db_session, "finished-at-owner@example.com")
    payer, _ = await accounts.register(
        db_session,
        "finished-at-payer@example.com",
        "strong-password-123",
        None,
    )
    request = await streaming.request_private_session(
        db_session,
        payer,
        profile.id,
        PrivateSessionMode.one_to_one,
    )
    session = await streaming.accept_private_request(db_session, owner, request.id)
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=60)
    session.status = PrivateSessionStatus.active
    session.accepted_at = base - timedelta(seconds=1)
    session.ready_at = base - timedelta(seconds=1)
    session.active_started_at = base

    settled = await streaming.process_livekit_webhook(
        db_session,
        {
            "id": "delayed-private-room-finished",
            "event": "room_finished",
            "createdAt": str(int((base + timedelta(seconds=15)).timestamp())),
            "room": {"name": session.provider_room_name},
        },
    )

    assert settled is not None and settled.status is PrivateSessionStatus.settled
    assert settled.billable_seconds == 15
    assert settled.ended_at == base + timedelta(seconds=15)
    provider_event = await db_session.scalar(
        select(ProviderLiveEvent).where(
            ProviderLiveEvent.external_event_id == "delayed-private-room-finished"
        )
    )
    assert provider_event is not None and provider_event.processed_at > settled.ended_at


@pytest.mark.asyncio
async def test_private_ending_retry_preserves_original_financial_cutoff_and_actor(
    db_session,
    livekit_control,
    monkeypatch,
):
    owner, profile = await creator(db_session, "ending-cutoff-owner@example.com")
    payer, _ = await accounts.register(
        db_session,
        "ending-cutoff-payer@example.com",
        "strong-password-123",
        None,
    )
    request = await streaming.request_private_session(
        db_session,
        payer,
        profile.id,
        PrivateSessionMode.one_to_one,
    )
    session = await streaming.accept_private_request(db_session, owner, request.id)
    base = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=30)
    cutoff = base + timedelta(seconds=10)
    session.status = PrivateSessionStatus.active
    session.accepted_at = base - timedelta(seconds=1)
    session.ready_at = base - timedelta(seconds=1)
    session.active_started_at = base

    class FailingControl:
        async def close_room(self, _room_name):
            raise StreamingProviderError("provider unavailable")

        async def remove_participant(self, _room_name, _identity):
            raise StreamingProviderError("provider unavailable")

    monkeypatch.setattr(streaming, "livekit_control_provider", FailingControl)
    pending = await streaming.end_private_session(
        db_session,
        owner,
        session.id,
        "ended_by_creator",
        cutoff,
    )
    assert pending.status is PrivateSessionStatus.ending
    assert pending.ended_at == cutoff
    assert pending.end_reason == "ended_by_creator"
    assert pending.ended_by_user_id == owner.id
    assert pending.billable_seconds == 10

    monkeypatch.setattr(streaming, "livekit_control_provider", lambda: livekit_control)
    settled = await streaming.end_private_session(
        db_session,
        None,
        session.id,
        "compliance_authority_unavailable",
        cutoff + timedelta(minutes=5),
        provider_room_closed=True,
    )
    assert settled.status is PrivateSessionStatus.settled
    assert settled.ended_at == cutoff
    assert settled.end_reason == "ended_by_creator"
    assert settled.ended_by_user_id == owner.id
    assert settled.billable_seconds == 10
    transaction = await db_session.scalar(
        select(LedgerTransaction).where(
            LedgerTransaction.reference == f"private_session:{session.id}"
        )
    )
    assert transaction is not None
    assert transaction.metadata_json["billable_seconds"] == "10"


@pytest.mark.asyncio
async def test_terminal_private_session_rejects_delayed_provider_events_and_reconciliation(
    db_session,
    livekit_control,
):
    owner, profile = await creator(db_session, "terminal-event-owner@example.com")
    payer, _ = await accounts.register(
        db_session,
        "terminal-event-payer@example.com",
        "strong-password-123",
        None,
        country_code="PT",
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
    assert session.status is PrivateSessionStatus.ending
    await db_session.commit()
    assert await process_committed_live_controls() == 1
    await db_session.refresh(session)
    assert session.status is PrivateSessionStatus.settled

    await streaming.process_private_provider_event(
        db_session,
        event_id="late-leave-after-settlement",
        event_type="participant_left",
        session_id=session.id,
        user_id=payer.id,
        now=datetime(2026, 8, 21, 0, 0, 2, tzinfo=UTC),
    )
    await streaming.process_livekit_webhook(
        db_session,
        {
            "id": "late-join-after-settlement",
            "event": "participant_joined",
            "createdAt": str(int(datetime.now(UTC).timestamp())),
            "room": {"name": session.provider_room_name},
            "participant": {"identity": str(payer.id)},
        },
    )
    await db_session.commit()
    assert await process_committed_live_controls() == 1
    assert livekit_control.closed_rooms == [
        session.provider_room_name,
        session.provider_room_name,
    ]
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
        db_session, "settle-payer@example.com", "strong-password-123", None, country_code="PT"
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
    livekit_control,
):
    owner, profile = await creator(db_session, "early-reversal-owner@example.com")
    payer, _ = await accounts.register(
        db_session,
        "early-reversal-payer@example.com",
        "strong-password-123",
        None,
        country_code="PT",
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
    assert livekit_control.closed_rooms == []
    await db_session.commit()
    assert await process_committed_live_controls() == 1
    assert livekit_control.closed_rooms == [session.provider_room_name]
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
async def test_signed_private_disputes_deny_access_and_reverse_exact_settlement_once(
    db_session,
    livekit_control,
):
    owner, profile = await creator(db_session, "signed-live-dispute-owner@example.com")
    pending_payer, _ = await accounts.register(
        db_session,
        "signed-live-pending@example.com",
        "strong-password-123",
        None,
        country_code="PT",
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
    assert livekit_control.closed_rooms == []
    await db_session.commit()
    assert await process_committed_live_controls() == 1
    assert livekit_control.closed_rooms == [pending_session.provider_room_name]
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
        db_session,
        "signed-live-settled@example.com",
        "strong-password-123",
        None,
        country_code="PT",
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
    await db_session.commit()
    assert await process_committed_live_controls() == 1
    assert livekit_control.closed_rooms == [
        pending_session.provider_room_name,
        settled_session.provider_room_name,
    ]
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
        db_session, "report-viewer@example.com", "strong-password-123", None, country_code="PT"
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
