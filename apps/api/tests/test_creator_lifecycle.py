import pytest
from sqlalchemy import select

from app.accounts import service as accounts
from app.creators import service
from app.models.audit import AuditEvent
from app.models.creator import CreatorStatus


@pytest.mark.asyncio
async def test_creator_application_requires_verified_adult_before_review(db_session):
    user, _ = await accounts.register(
        db_session, "creator@example.com", "strong-password-123", None
    )
    profile = await service.get_or_create_profile(db_session, user)
    await service.update_profile(
        db_session, profile, {"username": "creator-one", "display_name": "Creator One"}, user.id
    )
    await service.submit(db_session, profile, user.id)
    assert profile.status is CreatorStatus.pending_verification
    await service.development_verify(db_session, profile, True, user.id)
    assert profile.status is CreatorStatus.pending_review
    await service.set_status(db_session, profile, CreatorStatus.approved, user.id)
    profile.is_public = True
    await db_session.commit()
    assert "creator" in {role.name for role in user.roles}
    events = (await db_session.scalars(select(AuditEvent))).all()
    assert {
        "creator.application_submitted",
        "creator.status_approved",
        "creator.verification_changed",
    } <= {event.event_type for event in events}


@pytest.mark.asyncio
async def test_reserved_username_and_invalid_transition_are_rejected(db_session):
    user, _ = await accounts.register(db_session, "viewer@example.com", "strong-password-123", None)
    profile = await service.get_or_create_profile(db_session, user)
    with pytest.raises(ValueError):
        await service.update_profile(db_session, profile, {"username": "admin"}, user.id)
    with pytest.raises(ValueError):
        await service.set_status(db_session, profile, CreatorStatus.approved, user.id)
