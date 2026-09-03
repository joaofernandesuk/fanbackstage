import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import func, select
from surface_policy_helpers import publish_creator_identity_policy

from app.accounts import service as accounts
from app.api.routes import creators as creator_routes
from app.creators import service
from app.main import app
from app.models.audit import AuditEvent
from app.models.content import (
    AccessPolicy,
    ContentItem,
    ContentStatus,
    ContentType,
    DerivativeType,
    MediaAsset,
    MediaAudience,
    MediaDerivative,
    MediaStatus,
    MediaType,
    ModerationStatus,
)
from app.models.creator import (
    CreatorCategory,
    CreatorLanguage,
    CreatorProfile,
    CreatorProfileMedia,
    CreatorStatus,
    CreatorStatusHistory,
    CreatorVerification,
    VerificationStatus,
)
from app.models.identity import User
from app.models.messaging import UserBlock
from app.models.notification import InAppNotification


@pytest.mark.asyncio
async def test_profile_media_requires_owned_ready_public_derivative_and_replaces_safely(db_session):
    user, _ = await accounts.register(
        db_session, "profile-media@example.com", "strong-password-123", None, country_code="PT"
    )
    profile = await service.get_or_create_profile(db_session, user)
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        moderation_status=ModerationStatus.approved,
        audience=MediaAudience.safe_public,
        storage_key="original/profile-media",
        original_filename="avatar.jpg",
        mime_type="image/jpeg",
    )
    db_session.add(asset)
    await db_session.flush()
    db_session.add(
        MediaDerivative(
            media_asset_id=asset.id,
            derivative_type=DerivativeType.display,
            status=MediaStatus.ready,
            storage_key="display/profile-media",
            mime_type="image/jpeg",
        )
    )
    await db_session.flush()
    assigned = await service.set_profile_media(
        db_session,
        profile,
        user.id,
        kind="avatar",
        media_asset_id=asset.id,
        focal_x=0.25,
        focal_y=0.75,
    )
    assert assigned.focal_x == 0.25 and assigned.focal_y == 0.75
    assert await db_session.scalar(
        select(CreatorProfileMedia).where(CreatorProfileMedia.media_asset_id == asset.id)
    )
    assert await service.remove_profile_media(db_session, profile, user.id, kind="avatar")


async def mark_email_verified(db_session, email: str) -> None:
    user = await db_session.scalar(select(User).where(User.email == email))
    assert user
    user.email_verified_at = accounts._now()
    await db_session.commit()


@pytest.mark.asyncio
async def test_creator_application_is_concurrency_safe_and_audited_once(
    db_session, monkeypatch, reviewed_pt_compliance_policy
):
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
                    "country_code": "PT",
                    "legal_version_ids": [],
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
    registered_user_id = await db_session.scalar(
        select(User.id).where(User.email == "creator-concurrent@example.com")
    )
    assert event.actor_user_id == registered_user_id
    assert event.target_type == "creator_profile"
    assert event.target_id == first.json()["id"]


@pytest.mark.asyncio
async def test_creator_registration_policy_denial_creates_no_application(db_session, monkeypatch):
    async def denied(*_args, **_kwargs):
        return SimpleNamespace(
            allowed=False,
            code="FEATURE_UNAVAILABLE",
            action="CONTACT_SUPPORT",
            reason="Creator registration is unavailable",
        )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.post(
                "/api/v1/auth/register",
                json={
                    "email": "creator-registration-denied@example.com",
                    "password": "strong-password-123",
                    "adult_confirmed": True,
                    "country_code": "PT",
                    "legal_version_ids": [],
                },
            )
        ).status_code == 201
        await mark_email_verified(db_session, "creator-registration-denied@example.com")
        assert (
            await client.post(
                "/api/v1/auth/login",
                json={
                    "email": "creator-registration-denied@example.com",
                    "password": "strong-password-123",
                },
            )
        ).status_code == 200
        monkeypatch.setattr(creator_routes, "resolve_request_compliance_decision", denied)
        response = await client.post("/api/v1/creators/me/application")

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "FEATURE_UNAVAILABLE"
    user_id = await db_session.scalar(
        select(User.id).where(User.email == "creator-registration-denied@example.com")
    )
    assert (
        await db_session.scalar(select(CreatorProfile.id).where(CreatorProfile.user_id == user_id))
        is None
    )
    assert (
        await db_session.scalar(
            select(AuditEvent.id).where(
                AuditEvent.event_type == "creator.application_started",
                AuditEvent.actor_user_id == user_id,
            )
        )
        is None
    )


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
async def test_creator_self_projects_current_policy_eligibility_separately_from_raw_evidence(
    db_session,
):
    await publish_creator_identity_policy(db_session)
    now = datetime.now(UTC)
    user, _ = await accounts.register(
        db_session,
        "creator-self-effective@example.com",
        "strong-password-123",
        None,
        adult_confirmed=True,
        country_code="PT",
    )
    user.email_verified_at = now
    profile = CreatorProfile(
        user_id=user.id,
        username="creator-self-effective",
        display_name="Creator Self Effective",
        country_code="PT",
        status=CreatorStatus.approved,
        is_public=True,
    )
    db_session.add(profile)
    await db_session.flush()
    db_session.add(
        CreatorVerification(
            creator_profile_id=profile.id,
            provider="test-raw-current",
            provider_reference="creator-self-effective-raw-current",
            status=VerificationStatus.verified,
            identity_verified=True,
            adult_verified=True,
            country_code="PT",
            verified_at=now - timedelta(days=31),
            expires_at=now + timedelta(days=30),
        )
    )
    db_session.add(
        ContentItem(
            owner_creator_id=profile.id,
            created_by_user_id=user.id,
            content_type=ContentType.gallery,
            status=ContentStatus.draft,
            access_policy=AccessPolicy.free,
            title="Creator self unresolved consent",
            requires_verified_consent=True,
        )
    )
    await db_session.commit()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        assert (
            await client.post(
                "/api/v1/auth/login",
                json={
                    "email": user.email,
                    "password": "strong-password-123",
                },
            )
        ).status_code == 200
        response = await client.get("/api/v1/creators/me")

    assert response.status_code == 200
    payload = response.json()
    assert payload["verification_status"] == "verified"
    assert payload["adult_verified"] is True
    assert payload["creator_compliance"]["verification_status"] == "verified"
    assert payload["creator_compliance"]["identity_allowed"] is False
    assert payload["creator_compliance"]["public_allowed"] is False
    assert payload["creator_compliance"]["code"] == "CREATOR_IDENTITY_VERIFICATION_REQUIRED"
    assert payload["creator_compliance"]["payout_allowed"] is False
    assert payload["performer_consent_issue_count"] == 1
    assert payload["creator_compliance_action_required"] is True


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
async def test_creator_handle_availability_reserves_public_and_historic_handles(db_session):
    first_user, _ = await accounts.register(
        db_session, "handle-owner@example.com", "strong-password-123", None
    )
    first_profile = await service.get_or_create_profile(db_session, first_user)
    await service.update_profile(
        db_session,
        first_profile,
        {"username": "mercy-afterdark", "display_name": "Mercy After Dark"},
        first_user.id,
    )
    await db_session.commit()

    assert await service.username_availability(
        db_session, "mercy-afterdark", creator_profile_id=first_profile.id
    ) == ("mercy-afterdark", True)
    assert await service.username_availability(db_session, "mercy-afterdark") == (
        "mercy-afterdark",
        False,
    )
    assert await service.username_availability(db_session, "mercy afterdark") == (
        "mercy afterdark",
        False,
    )
    assert await service.username_availability(db_session, "a-new-handle") == (
        "a-new-handle",
        True,
    )


@pytest.mark.asyncio
async def test_creator_profile_taxonomy_and_social_fields_persist_with_enabled_validation(
    db_session,
):
    db_session.add_all(
        [
            CreatorCategory(slug="live-shows", label="Live shows", enabled=True, position=20),
            CreatorCategory(
                slug="cosplay-fantasy", label="Cosplay & fantasy", enabled=True, position=10
            ),
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
        country_code="PT",
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
            "cosplay-fantasy",
            "live-shows",
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
                "category_slugs": ["LIVE-SHOWS", "cosplay-fantasy"],
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
            "cosplay-fantasy",
            "live-shows",
        ]
        assert [item["code"] for item in saved.json()["languages"]] == ["en", "pt"]
        assert saved.json()["social_links"] == [
            {"label": "Portfolio", "url": "https://creator.example/portfolio"},
            {"label": "Updates", "url": "https://social.example/updates"},
        ]

        replaced = await client.patch(
            "/api/v1/creators/me",
            json={
                "category_slugs": ["live-shows"],
                "language_codes": ["pt"],
                "social_links": [
                    {"label": "Latest updates", "url": "https://social.example/updates"}
                ],
            },
        )
        assert replaced.status_code == 200
        assert [item["code"] for item in replaced.json()["categories"]] == ["live-shows"]
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
        assert [item["code"] for item in persisted.json()["categories"]] == ["live-shows"]
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
async def test_development_kyc_http_requires_explicit_nonproduction_opt_in(
    db_session, monkeypatch, reviewed_pt_compliance_policy
):
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
                    "country_code": "PT",
                    "legal_version_ids": [],
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
    db_session, monkeypatch, reviewed_pt_compliance_policy
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
                    "country_code": "PT",
                    "legal_version_ids": [],
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
            db_session,
            "admin@example.com",
            "strong-password-123",
            None,
            country_code="PT",
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
        assert published.json()["creator_compliance"]["payout_kyc_required"] is False
        assert published.json()["creator_compliance"]["payout_kyc_satisfied"] is True
        assert published.json()["creator_compliance"]["payout_allowed"] is False
        assert published.json()["creator_compliance"]["payout_code"] == "PAYOUT_NOT_CONFIGURED"
        assert published.json()["performer_consent_issue_count"] == 0
        assert published.json()["creator_compliance_action_required"] is False
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
            "adult_access_required": True,
            "adult_access_granted": True,
            "compliance_allowed": True,
            "compliance_code": "ALLOWED",
            "compliance_action": None,
            "compliance_reason": "Policy and age-assurance requirements are satisfied",
        }
        original_resolver = creator_routes.resolve_request_compliance_decision

        async def denied_profile(*_args, **_kwargs):
            return SimpleNamespace(
                allowed=False,
                age_access_allowed=False,
                code="AGE_VERIFICATION_REQUIRED",
                action="VERIFY_AGE",
                reason="Age verification is required",
            )

        monkeypatch.setattr(creator_routes, "resolve_request_compliance_decision", denied_profile)
        restricted_profile = await creator_client.get("/api/v1/creators/creator-public")
        assert restricted_profile.status_code == 200
        restricted_payload = restricted_profile.json()
        assert restricted_payload["display_name"] == "Creator Public"
        assert restricted_payload["username"] == "creator-public"
        assert restricted_payload["bio"] is None
        assert restricted_payload["avatar_reference"] is None
        assert restricted_payload["cover_reference"] is None
        assert restricted_payload["languages"] == []
        assert restricted_payload["categories"] == []
        assert restricted_payload["social_links"] == []
        assert restricted_payload["follower_count"] == 0
        assert restricted_payload["compliance_code"] == "AGE_VERIFICATION_REQUIRED"
        assert "A public-safe profile" not in str(restricted_payload)
        monkeypatch.setattr(
            creator_routes,
            "resolve_request_compliance_decision",
            original_resolver,
        )
        viewer, _ = await accounts.register(
            db_session,
            "creator-public-viewer@example.com",
            "strong-password-123",
            None,
            adult_confirmed=True,
            country_code="PT",
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
        # The reviewed fixture does not require creator age/KYC. A provider row
        # is not itself a policy switch; public eligibility follows the current
        # reviewed creator rules.
        assert (await creator_client.get("/api/v1/creators/creator-public")).status_code == 200
        creator = await db_session.scalar(select(User).where(User.email == creator_email))
        assert creator is not None
        await db_session.refresh(creator, ["roles"])
        assert "creator" in {role.name for role in creator.roles}
        events = (await db_session.scalars(select(AuditEvent))).all()
        assert {"creator.status_approved", "role.assigned"} <= {
            event.event_type for event in events
        }


@pytest.mark.asyncio
async def test_admin_creator_queue_notifies_reviewers_and_records_decision_reason(
    db_session, monkeypatch, reviewed_pt_compliance_policy
):
    """A verified creator application becomes an actionable, audited admin queue item."""
    from app.core.config import Settings

    monkeypatch.setattr(
        "app.api.routes.creators.get_settings",
        lambda: Settings(environment="test", development_kyc_http_enabled=True),
    )
    admin, _ = await accounts.register(
        db_session,
        "creator-queue-admin@example.com",
        "strong-password-123",
        None,
        country_code="PT",
    )
    admin.email_verified_at = accounts._now()
    await accounts.assign_role(db_session, admin, "admin", admin.id, None)
    await db_session.commit()

    async with (
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as applicant_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as admin_client,
    ):
        applicant_email = "creator-queue-applicant@example.com"
        assert (
            await applicant_client.post(
                "/api/v1/auth/register",
                json={
                    "email": applicant_email,
                    "password": "strong-password-123",
                    "adult_confirmed": True,
                    "country_code": "PT",
                    "legal_version_ids": [],
                },
            )
        ).status_code == 201
        await mark_email_verified(db_session, applicant_email)
        assert (
            await applicant_client.post(
                "/api/v1/auth/login",
                json={"email": applicant_email, "password": "strong-password-123"},
            )
        ).status_code == 200
        assert (await applicant_client.post("/api/v1/creators/me/application")).status_code == 200
        assert (
            await applicant_client.patch(
                "/api/v1/creators/me",
                json={"username": "creator-queue", "display_name": "Queue Creator"},
            )
        ).status_code == 200
        assert (await applicant_client.post("/api/v1/creators/me/submit")).status_code == 200
        started_notification = await db_session.scalar(
            select(InAppNotification).where(
                InAppNotification.recipient_user_id == admin.id,
                InAppNotification.notification_type == "CREATOR_APPLICATION_KYC_STARTED",
            )
        )
        assert started_notification is not None
        assert started_notification.target_path == "/admin/creators?status=pending_verification"
        assert (
            await applicant_client.post("/api/v1/creators/me/verification/development")
        ).status_code == 200

        notification = await db_session.scalar(
            select(InAppNotification).where(
                InAppNotification.recipient_user_id == admin.id,
                InAppNotification.notification_type == "CREATOR_APPLICATION_REVIEW_REQUIRED",
            )
        )
        assert notification is not None
        assert notification.target_path == "/admin/creators?status=pending_review"
        assert applicant_email not in notification.body
        assert (await applicant_client.get("/api/v1/admin/operations/overview")).status_code == 403

        assert (
            await admin_client.post(
                "/api/v1/auth/login",
                json={"email": admin.email, "password": "strong-password-123"},
            )
        ).status_code == 200
        overview = await admin_client.get("/api/v1/admin/operations/overview")
        assert overview.status_code == 200
        assert any(
            queue["key"] == "creator_review" and queue["count"] == 1
            for queue in overview.json()["queues"]
        )
        queue = await admin_client.get("/api/v1/admin/creator-applications?status=pending_review")
        assert queue.status_code == 200
        application = queue.json()[0]
        assert application["email"] == applicant_email
        assert application["review_ready"] is True
        assert application["verification"]["status"] == "verified"
        approved = await admin_client.post(
            f"/api/v1/admin/creator-applications/{application['id']}/approve",
            json={"reason": "Identity and application details reviewed."},
        )
        assert approved.status_code == 200

    profile = await db_session.scalar(
        select(CreatorProfile).where(CreatorProfile.username == "creator-queue")
    )
    assert profile is not None
    assert profile.status is CreatorStatus.approved
    history = await db_session.scalar(
        select(CreatorStatusHistory)
        .where(CreatorStatusHistory.creator_profile_id == profile.id)
        .order_by(CreatorStatusHistory.created_at.desc())
    )
    assert history is not None
    assert history.reason == "Identity and application details reviewed."
