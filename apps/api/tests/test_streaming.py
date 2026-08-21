from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.accounts import service as accounts
from app.creators import service as creators
from app.models.creator import CreatorStatus
from app.models.streaming import PrivateSession, PrivateSessionMode, PrivateSessionStatus
from app.streaming import service as streaming


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
