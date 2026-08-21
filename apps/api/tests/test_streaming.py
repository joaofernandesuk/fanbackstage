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
    assert session.payment_attempt_id is None
    assert session.provider_room_name
