import httpx
import pytest
from sqlalchemy import select

from app.accounts import service as accounts
from app.creators import service
from app.main import app
from app.models.audit import AuditEvent
from app.models.creator import CreatorProfile, CreatorStatus
from app.models.identity import User


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


@pytest.mark.asyncio
async def test_admin_approval_allows_owner_to_publish_a_public_safe_profile(db_session):
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as creator_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as admin_client,
    ):
        creator_email = "creator-public@example.com"
        assert (
            await creator_client.post(
                "/api/v1/auth/register",
                json={"email": creator_email, "password": "strong-password-123"},
            )
        ).status_code == 201
        assert (
            await creator_client.post(
                "/api/v1/auth/login",
                json={"email": creator_email, "password": "strong-password-123"},
            )
        ).status_code == 200
        application = await creator_client.post("/api/v1/creators/me/application")
        assert application.status_code == 200
        assert (
            await creator_client.patch(
                "/api/v1/creators/me",
                json={
                    "username": "creator-public",
                    "display_name": "Creator Public",
                    "bio": "A public-safe profile.",
                },
            )
        ).status_code == 200
        assert (await creator_client.post("/api/v1/creators/me/submit")).status_code == 200
        assert (
            await creator_client.post("/api/v1/creators/me/verification/development")
        ).status_code == 200
        assert (await creator_client.get("/api/v1/creators/creator-public")).status_code == 404

        admin, _ = await accounts.register(
            db_session, "admin@example.com", "strong-password-123", None
        )
        await accounts.assign_role(db_session, admin, "admin", admin.id, None)
        await db_session.commit()
        assert (
            await admin_client.post(
                "/api/v1/auth/login",
                json={"email": "admin@example.com", "password": "strong-password-123"},
            )
        ).status_code == 200
        profile = await db_session.scalar(
            select(CreatorProfile).where(CreatorProfile.username == "creator-public")
        )
        assert profile is not None
        assert (
            await admin_client.post(f"/api/v1/admin/creator-applications/{profile.id}/approve")
        ).status_code == 200

        published = await creator_client.patch("/api/v1/creators/me", json={"is_public": True})
        assert published.status_code == 200
        public = await creator_client.get("/api/v1/creators/creator-public")
        assert public.status_code == 200
        assert public.json() == {
            "id": str(profile.id),
            "username": "creator-public",
            "display_name": "Creator Public",
            "bio": "A public-safe profile.",
            "avatar_reference": None,
            "cover_reference": None,
            "location": None,
            "timezone": None,
            "verified": True,
            "follower_count": 0,
            "languages": [],
            "categories": [],
            "social_links": [],
        }
        creator = await db_session.scalar(select(User).where(User.email == creator_email))
        assert creator is not None
        await db_session.refresh(creator, ["roles"])
        assert "creator" in {role.name for role in creator.roles}
        events = (await db_session.scalars(select(AuditEvent))).all()
        assert {"creator.status_approved", "role.assigned"} <= {
            event.event_type for event in events
        }
