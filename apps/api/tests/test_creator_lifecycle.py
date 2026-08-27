import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.accounts import service as accounts
from app.creators import service
from app.main import app
from app.models.audit import AuditEvent
from app.models.creator import (
    CreatorCategory,
    CreatorLanguage,
    CreatorProfile,
    CreatorStatus,
    CreatorVerification,
    VerificationStatus,
)
from app.models.identity import User
from app.models.messaging import UserBlock


async def mark_email_verified(db_session, email: str) -> None:
    user = await db_session.scalar(select(User).where(User.email == email))
    assert user
    user.email_verified_at = accounts._now()
    await db_session.commit()


@pytest.mark.asyncio
async def test_creator_application_is_concurrency_safe_and_audited_once(db_session, monkeypatch):
    initial_lookup_barrier = asyncio.Barrier(2)
    original_profile_for_user = service.profile_for_user

    async def synchronized_initial_lookup(db, user_id):
        profile = await original_profile_for_user(db, user_id)
        if profile is None:
            await asyncio.wait_for(initial_lookup_barrier.wait(), timeout=5)
        return profile

    monkeypatch.setattr(service, "profile_for_user", synchronized_initial_lookup)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "creator-concurrent@example.com",
                    "password": "strong-password-123",
                    "adult_confirmed": True,
                },
            )
        ).status_code == 201
        await mark_email_verified(db_session, "creator-concurrent@example.com")
        assert (
            await client.post(
                "/api/v1/auth/login",
                json={
                    "email": "creator-concurrent@example.com",
                    "password": "strong-password-123",
                },
            )
        ).status_code == 200

        first, second = await asyncio.gather(
            client.post("/api/v1/creators/me/application"),
            client.post("/api/v1/creators/me/application"),
        )

    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert (await db_session.scalar(select(func.count()).select_from(CreatorProfile))) == 1
    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "creator.application_started")
    )
    assert event is not None
    assert event.actor_user_id == (await db_session.scalar(select(User.id)))
    assert event.target_type == "creator_profile"
    assert event.target_id == first.json()["id"]


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
    for unsafe_url in (
        "javascript:alert(1)",
        "https://user:password@example.com/profile",
        "https://example.com\\@untrusted.example/profile",
    ):
        with pytest.raises(ValueError, match="valid HTTP or HTTPS URL"):
            await service.update_profile(
                db_session,
                profile,
                {"social_links": [{"label": "Unsafe", "url": unsafe_url}]},
                user.id,
            )
    assert profile.links == []


@pytest.mark.asyncio
async def test_creator_profile_taxonomy_and_social_fields_persist_with_enabled_validation(
    db_session,
):
    db_session.add_all(
        [
            CreatorCategory(slug="studio", label="Studio", enabled=True, position=20),
            CreatorCategory(slug="cosplay", label="Cosplay", enabled=True, position=10),
            CreatorCategory(slug="disabled-category", label="Disabled", enabled=False),
            CreatorLanguage(code="en", label="English", enabled=True),
            CreatorLanguage(code="pt", label="Portuguese", enabled=True),
            CreatorLanguage(code="disabled", label="Disabled", enabled=False),
        ]
    )
    await db_session.commit()
    user, _ = await accounts.register(
        db_session,
        "profile-fields@example.com",
        "strong-password-123",
        None,
        adult_confirmed=True,
    )
    await mark_email_verified(db_session, user.email)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.post(
                "/api/v1/auth/login",
                json={
                    "email": "profile-fields@example.com",
                    "password": "strong-password-123",
                },
            )
        ).status_code == 200
        started = await client.post("/api/v1/creators/me/application")
        assert started.status_code == 200
        assert [item["code"] for item in started.json()["available_categories"]] == [
            "cosplay",
            "studio",
        ]
        assert [item["code"] for item in started.json()["available_languages"]] == [
            "en",
            "pt",
        ]

        saved = await client.patch(
            "/api/v1/creators/me",
            json={
                "username": "profile-fields",
                "display_name": "Profile Fields",
                "bio": "Authoritative creator profile fields.",
                "country_code": "pt",
                "region": "Lisbon",
                "city": "Lisbon",
                "show_location": True,
                "timezone": "Europe/Lisbon",
                "category_slugs": ["STUDIO", "cosplay"],
                "language_codes": ["PT", "en"],
                "social_links": [
                    {"label": "Portfolio", "url": "https://creator.example/portfolio"},
                    {"label": "Updates", "url": "https://social.example/updates"},
                ],
            },
        )
        assert saved.status_code == 200
        assert saved.json()["country_code"] == "PT"
        assert [item["code"] for item in saved.json()["categories"]] == [
            "cosplay",
            "studio",
        ]
        assert [item["code"] for item in saved.json()["languages"]] == ["en", "pt"]
        assert saved.json()["social_links"] == [
            {"label": "Portfolio", "url": "https://creator.example/portfolio"},
            {"label": "Updates", "url": "https://social.example/updates"},
        ]

        replaced = await client.patch(
            "/api/v1/creators/me",
            json={
                "category_slugs": ["studio"],
                "language_codes": ["pt"],
                "social_links": [
                    {"label": "Latest updates", "url": "https://social.example/updates"}
                ],
            },
        )
        assert replaced.status_code == 200
        assert [item["code"] for item in replaced.json()["categories"]] == ["studio"]
        assert [item["code"] for item in replaced.json()["languages"]] == ["pt"]
        assert replaced.json()["social_links"] == [
            {"label": "Latest updates", "url": "https://social.example/updates"}
        ]

        unavailable = await client.patch(
            "/api/v1/creators/me", json={"category_slugs": ["disabled-category"]}
        )
        assert unavailable.status_code == 400
        assert unavailable.json()["detail"] == "Category selections include unavailable values"
        duplicate = await client.patch("/api/v1/creators/me", json={"language_codes": ["pt", "PT"]})
        assert duplicate.status_code == 400
        assert duplicate.json()["detail"] == "Language selections cannot contain duplicates"
        unsafe_link = await client.patch(
            "/api/v1/creators/me",
            json={"social_links": [{"label": "Unsafe", "url": "javascript:alert(1)"}]},
        )
        assert unsafe_link.status_code == 422

        persisted = await client.get("/api/v1/creators/me")
        assert persisted.status_code == 200
        assert [item["code"] for item in persisted.json()["categories"]] == ["studio"]
        assert [item["code"] for item in persisted.json()["languages"]] == ["pt"]
        assert persisted.json()["social_links"] == [
            {"label": "Latest updates", "url": "https://social.example/updates"}
        ]

        cleared = await client.patch(
            "/api/v1/creators/me",
            json={
                "bio": None,
                "country_code": None,
                "region": None,
                "city": None,
                "show_location": False,
                "timezone": None,
            },
        )
        assert cleared.status_code == 200
        assert cleared.json()["bio"] is None
        assert cleared.json()["country_code"] is None
        assert cleared.json()["region"] is None
        assert cleared.json()["city"] is None
        assert cleared.json()["show_location"] is False
        assert cleared.json()["timezone"] is None


@pytest.mark.asyncio
async def test_development_kyc_http_requires_explicit_nonproduction_opt_in(db_session, monkeypatch):
    from app.core.config import Settings

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "dev-kyc-guard@example.com",
                    "password": "strong-password-123",
                    "adult_confirmed": True,
                },
            )
        ).status_code == 201
        await mark_email_verified(db_session, "dev-kyc-guard@example.com")
        assert (
            await client.post(
                "/api/v1/auth/login",
                json={
                    "email": "dev-kyc-guard@example.com",
                    "password": "strong-password-123",
                },
            )
        ).status_code == 200
        await client.post("/api/v1/creators/me/application")
        await client.patch(
            "/api/v1/creators/me",
            json={"username": "dev-kyc-guard", "display_name": "Dev KYC Guard"},
        )
        submitted = await client.post("/api/v1/creators/me/submit")
        assert submitted.status_code == 200
        assert submitted.json()["development_verification_available"] is False

        assert (
            await client.post("/api/v1/creators/me/verification/development")
        ).status_code == 404
        monkeypatch.setattr(
            "app.api.routes.creators.get_settings",
            lambda: Settings(environment="staging"),
        )
        assert (await client.get("/api/v1/creators/me")).json()[
            "development_verification_available"
        ] is False
        assert (
            await client.post("/api/v1/creators/me/verification/development")
        ).status_code == 404


@pytest.mark.asyncio
async def test_admin_approval_allows_owner_to_publish_a_public_safe_profile(
    db_session, monkeypatch
):
    from app.core.config import Settings

    monkeypatch.setattr(
        "app.api.routes.creators.get_settings",
        lambda: Settings(environment="test", development_kyc_http_enabled=True),
    )
    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as creator_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as admin_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as viewer_client,
    ):
        creator_email = "creator-public@example.com"
        assert (
            await creator_client.post(
                "/api/v1/auth/register",
                json={
                    "email": creator_email,
                    "password": "strong-password-123",
                    "adult_confirmed": True,
                },
            )
        ).status_code == 201
        await mark_email_verified(db_session, creator_email)
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
        submitted = await creator_client.post("/api/v1/creators/me/submit")
        assert submitted.status_code == 200
        assert submitted.json()["development_verification_available"] is True
        assert (
            await creator_client.post("/api/v1/creators/me/verification/development")
        ).status_code == 200
        assert (await creator_client.get("/api/v1/creators/creator-public")).status_code == 404

        admin, _ = await accounts.register(
            db_session, "admin@example.com", "strong-password-123", None
        )
        admin.email_verified_at = accounts._now()
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
        viewer, _ = await accounts.register(
            db_session,
            "creator-public-viewer@example.com",
            "strong-password-123",
            None,
            adult_confirmed=True,
        )
        viewer.email_verified_at = accounts._now()
        await db_session.commit()
        assert (
            await viewer_client.post(
                "/api/v1/auth/login",
                json={
                    "email": viewer.email,
                    "password": "strong-password-123",
                },
            )
        ).status_code == 200
        assert (await viewer_client.get("/api/v1/creators/creator-public")).status_code == 200
        assert (
            await viewer_client.get("/api/v1/creators/creator-public/subscription-options")
        ).status_code == 200
        db_session.add(UserBlock(blocker_user_id=profile.user_id, blocked_user_id=viewer.id))
        await db_session.commit()
        assert (await viewer_client.get("/api/v1/creators/creator-public")).status_code == 404
        assert (
            await viewer_client.get("/api/v1/creators/creator-public/subscription-options")
        ).status_code == 404
        assert (await creator_client.get("/api/v1/creators/creator-public")).status_code == 200
        db_session.add(
            CreatorVerification(
                creator_profile_id=profile.id,
                provider="test-expiry",
                provider_reference="test-expired-current-verification",
                status=VerificationStatus.expired,
                adult_verified=False,
                created_at=datetime.now(UTC) + timedelta(seconds=1),
            )
        )
        await db_session.commit()
        assert (await creator_client.get("/api/v1/creators/creator-public")).status_code == 404
        creator = await db_session.scalar(select(User).where(User.email == creator_email))
        assert creator is not None
        await db_session.refresh(creator, ["roles"])
        assert "creator" in {role.name for role in creator.roles}
        events = (await db_session.scalars(select(AuditEvent))).all()
        assert {"creator.status_approved", "role.assigned"} <= {
            event.event_type for event in events
        }
