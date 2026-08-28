from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select

from app.accounts import adult_access, service
from app.core.config import get_settings
from app.main import app
from app.models.audit import AuditEvent
from app.models.identity import SecurityToken, TokenPurpose, User, UserSession
from app.models.notification import NotificationIntent


@pytest.fixture
async def client(reviewed_pt_compliance_policy):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as value:
        yield value


async def register(client: httpx.AsyncClient, email: str = "fan@example.com") -> httpx.Response:
    return await client.post(
        "/api/v1/auth/register",
        json={
            "email": email,
            "password": "strong-password-123",
            "adult_confirmed": True,
            "country_code": "PT",
            "legal_version_ids": [],
        },
    )


async def login(client: httpx.AsyncClient, email: str = "fan@example.com") -> httpx.Response:
    return await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "strong-password-123"}
    )


async def mark_email_verified(db_session, email: str = "fan@example.com") -> User:
    user = await db_session.scalar(select(User).where(User.email == email))
    assert user
    user.email_verified_at = service._now()
    await db_session.commit()
    return user


@pytest.mark.asyncio
async def test_registration_normalizes_email_rejects_duplicates_and_hides_password(
    client, db_session
):
    first = await register(client, " Fan@Example.COM ")
    assert first.status_code == 201
    assert first.json()["email"] == "fan@example.com"
    assert "password" not in first.text
    duplicate = await register(client)
    assert duplicate.status_code == 409
    events = (await db_session.scalars(select(AuditEvent))).all()
    assert [event.event_type for event in events] == ["account.registered"]


@pytest.mark.asyncio
async def test_registration_requires_explicit_adult_self_attestation(client):
    missing = await client.post(
        "/api/v1/auth/register",
        json={"email": "missing@example.com", "password": "strong-password-123"},
    )
    declined = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "declined@example.com",
            "password": "strong-password-123",
            "adult_confirmed": False,
            "country_code": "PT",
            "legal_version_ids": [],
        },
    )
    assert missing.status_code == declined.status_code == 422


@pytest.mark.asyncio
async def test_low_level_registration_omission_cannot_grant_adult_attestation(db_session):
    user, _ = await service.register(
        db_session, "internal-unattested@example.com", "strong-password-123", None
    )
    await db_session.flush()

    assert not adult_access.has_current_self_attestation(user)
    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "account.registered")
    )
    assert event and event.metadata_json["adult_assurance"] == "none"
    assert "adult_attestation_version" not in event.metadata_json


@pytest.mark.asyncio
async def test_login_logout_and_revoked_session_cannot_authenticate(client, db_session):
    await register(client)
    await mark_email_verified(db_session)
    assert (await login(client)).status_code == 200
    assert (await client.get("/api/v1/me")).status_code == 200
    assert (await client.post("/api/v1/auth/logout")).status_code == 200
    assert (await client.get("/api/v1/me")).status_code == 401


@pytest.mark.asyncio
async def test_configured_session_cookie_name_is_used_for_login_authentication_and_logout(
    client,
    db_session,
    monkeypatch,
):
    settings = get_settings().model_copy(update={"session_cookie_name": "custom_fan_session"})
    monkeypatch.setattr("app.api.deps.get_settings", lambda: settings)
    monkeypatch.setattr("app.api.routes.auth.get_settings", lambda: settings)

    await register(client, "custom-cookie@example.com")
    await mark_email_verified(db_session, "custom-cookie@example.com")
    response = await login(client, "custom-cookie@example.com")

    assert response.status_code == 200
    assert "custom_fan_session=" in response.headers["set-cookie"]
    assert (await client.get("/api/v1/me")).status_code == 200
    logout = await client.post("/api/v1/auth/logout")
    assert logout.status_code == 200
    assert "custom_fan_session=" in logout.headers["set-cookie"]
    assert (await client.get("/api/v1/me")).status_code == 401


@pytest.mark.asyncio
async def test_login_and_account_response_support_seeded_local_demo_email(client, db_session):
    email = "subscriber@demo.fanbackstage.local"
    user, _ = await service.register(
        db_session,
        email,
        "fanbackstage-demo-local-only",
        None,
        adult_confirmed=True,
    )
    user.email_verified_at = service._now()
    await db_session.commit()

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "fanbackstage-demo-local-only"},
    )
    assert response.status_code == 200
    assert response.json()["email"] == email
    assert (await client.get("/api/v1/me")).json()["email"] == email

    rejected = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "another@demo.fanbackstage.local",
            "password": "strong-password-123",
            "adult_confirmed": True,
            "country_code": "PT",
            "legal_version_ids": [],
        },
    )
    assert rejected.status_code == 422


@pytest.mark.asyncio
async def test_invalid_login_is_generic_and_audited(client, db_session):
    await register(client)
    response = await client.post(
        "/api/v1/auth/login", json={"email": "fan@example.com", "password": "wrong-password-123"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password"
    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "auth.login_failed")
    )
    assert event and "password" not in event.metadata_json and "token" not in event.metadata_json


@pytest.mark.asyncio
async def test_inactive_account_login_is_rejected_before_session_creation(client, db_session):
    user, _ = await service.register(
        db_session,
        "inactive@example.com",
        "strong-password-123",
        None,
        adult_confirmed=True,
    )
    user.is_active = False
    await db_session.commit()

    response = await login(client, "inactive@example.com")

    assert response.status_code == 403
    assert response.json()["detail"] == "This account is not active"
    assert (await db_session.scalar(select(func.count()).select_from(UserSession))) == 0
    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "auth.login_blocked_inactive")
    )
    assert event and event.actor_user_id == user.id


@pytest.mark.asyncio
async def test_unverified_email_login_is_rejected_before_session_creation(client, db_session):
    await register(client, "unverified@example.com")

    response = await login(client, "unverified@example.com")

    assert response.status_code == 403
    assert response.json()["detail"] == "Verify your email address before logging in."
    assert (await db_session.scalar(select(func.count()).select_from(UserSession))) == 0
    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "auth.login_blocked_unverified")
    )
    assert event and event.target_id


@pytest.mark.asyncio
async def test_stale_unverified_session_cannot_authenticate_or_start_paid_action(
    client, db_session
):
    await register(client, "stale-session@example.com")
    user = await db_session.scalar(select(User).where(User.email == "stale-session@example.com"))
    assert user and user.email_verified_at is None
    raw = await service.create_session(db_session, user, None, "pre-gate-test")
    await db_session.commit()
    client.cookies.set(get_settings().session_cookie_name, raw)

    assert (await client.get("/api/v1/me")).status_code == 401
    purchase = await client.post(
        f"/api/v1/purchases/content/{uuid4()}",
        headers={"Idempotency-Key": "stale-unverified-session"},
    )
    assert purchase.status_code == 401


@pytest.mark.asyncio
async def test_resend_verification_is_non_enumerating_and_only_issues_when_needed(
    client, db_session
):
    await register(client, "resend@example.com")
    baseline_tokens = await db_session.scalar(select(func.count()).select_from(SecurityToken))
    baseline_intents = await db_session.scalar(select(func.count()).select_from(NotificationIntent))

    expected = {"message": "If the account needs verification, a new link has been sent."}
    existing = await client.post(
        "/api/v1/auth/resend-verification", json={"email": "resend@example.com"}
    )
    unknown = await client.post(
        "/api/v1/auth/resend-verification", json={"email": "unknown@example.com"}
    )
    assert existing.status_code == unknown.status_code == 200
    assert existing.json() == unknown.json() == expected
    assert (
        await db_session.scalar(select(func.count()).select_from(SecurityToken))
        == baseline_tokens + 1
    )
    assert (
        await db_session.scalar(select(func.count()).select_from(NotificationIntent))
        == baseline_intents + 1
    )
    assert await db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "auth.email_verification_resent")
    )

    user = await mark_email_verified(db_session, "resend@example.com")
    assert user.email_verified_at
    verified = await client.post(
        "/api/v1/auth/resend-verification", json={"email": "resend@example.com"}
    )
    assert verified.status_code == 200 and verified.json() == expected
    assert (
        await db_session.scalar(select(func.count()).select_from(SecurityToken))
        == baseline_tokens + 1
    )


@pytest.mark.asyncio
async def test_adult_access_cookie_is_signed_and_account_state_is_authoritative(client, db_session):
    anonymous = await client.post("/api/v1/auth/adult-access", json={"adult_confirmed": True})
    assert anonymous.status_code == 200
    assert anonymous.json()["source"] == "cookie"
    assert anonymous.json()["assurance"] == "self_attested"
    cookie_name = get_settings().adult_access_cookie_name
    signed = client.cookies.get(cookie_name)
    assert signed
    assert (await client.get("/api/v1/auth/adult-access/status")).json()["allowed"] is True

    client.cookies.set(cookie_name, f"{signed[:-1]}{'A' if signed[-1] != 'A' else 'B'}")
    tampered = await client.get("/api/v1/auth/adult-access/status")
    assert tampered.json() == {
        "allowed": False,
        "assurance": "none",
        "source": "none",
        "policy_version": adult_access.current_policy_version(),
        "expires_at": None,
    }

    user, _ = await service.register(
        db_session,
        "legacy@example.com",
        "strong-password-123",
        None,
        adult_confirmed=False,
    )
    user.email_verified_at = service._now()
    user.adult_attested_at = None
    user.adult_attestation_version = None
    await db_session.commit()
    assert (await login(client, "legacy@example.com")).status_code == 200
    client.cookies.set(cookie_name, signed)
    denied = await client.get("/api/v1/auth/adult-access/status")
    assert denied.json()["allowed"] is False
    acknowledged = await client.post("/api/v1/auth/adult-access", json={"adult_confirmed": True})
    assert acknowledged.json()["source"] == "account"
    await db_session.refresh(user)
    assert adult_access.has_current_self_attestation(user)


@pytest.mark.asyncio
async def test_session_listing_single_revoke_and_revoke_others(client, db_session):
    await register(client)
    await mark_email_verified(db_session)
    assert (await login(client)).status_code == 200
    second = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
    assert (await login(second)).status_code == 200
    sessions = await client.get("/api/v1/sessions")
    assert len(sessions.json()) == 2
    other = next(value for value in sessions.json() if not value["current"])
    assert (await client.delete(f"/api/v1/sessions/{other['id']}")).status_code == 200
    assert (await second.get("/api/v1/me")).status_code == 401
    await login(second)
    assert (await client.delete("/api/v1/sessions")).status_code == 200
    assert (await second.get("/api/v1/me")).status_code == 401
    await second.aclose()


@pytest.mark.asyncio
async def test_verification_token_is_hashed_single_use_and_expires(client, db_session):
    await register(client)
    user = await db_session.scalar(select(User).where(User.email == "fan@example.com"))
    raw = await service.issue_security_token(db_session, user.id, TokenPurpose.email_verification)
    await db_session.commit()
    stored = await db_session.scalar(
        select(SecurityToken).where(SecurityToken.secret_hash == service._digest(raw))
    )
    assert stored.secret_hash != raw
    assert (await client.post("/api/v1/auth/verify-email", json={"token": raw})).status_code == 200
    assert (await client.post("/api/v1/auth/verify-email", json={"token": raw})).status_code == 400
    expired = await service.issue_security_token(
        db_session, user.id, TokenPurpose.email_verification
    )
    row = await db_session.scalar(
        select(SecurityToken).where(SecurityToken.secret_hash == service._digest(expired))
    )
    row.expires_at = service._now() - timedelta(seconds=1)
    await db_session.commit()
    assert (
        await client.post("/api/v1/auth/verify-email", json={"token": expired})
    ).status_code == 400


@pytest.mark.asyncio
async def test_password_reset_is_single_use_expiring_and_revokes_sessions(client, db_session):
    await register(client)
    await mark_email_verified(db_session)
    await login(client)
    user = await db_session.scalar(select(User).where(User.email == "fan@example.com"))
    raw = await service.issue_security_token(db_session, user.id, TokenPurpose.password_reset)
    await db_session.commit()
    response = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw, "new_password": "new-strong-password-123"},
    )
    assert response.status_code == 200
    assert (await client.get("/api/v1/me")).status_code == 401
    assert (
        await client.post(
            "/api/v1/auth/reset-password",
            json={"token": raw, "new_password": "new-strong-password-123"},
        )
    ).status_code == 400
    expired = await service.issue_security_token(db_session, user.id, TokenPurpose.password_reset)
    row = await db_session.scalar(
        select(SecurityToken).where(SecurityToken.secret_hash == service._digest(expired))
    )
    row.expires_at = service._now() - timedelta(seconds=1)
    await db_session.commit()
    assert (
        await client.post(
            "/api/v1/auth/reset-password",
            json={"token": expired, "new_password": "new-strong-password-123"},
        )
    ).status_code == 400
