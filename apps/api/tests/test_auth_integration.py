from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from app.accounts import service
from app.main import app
from app.models.audit import AuditEvent
from app.models.identity import SecurityToken, TokenPurpose, User


@pytest.fixture
async def client():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as value:
        yield value


async def register(client: httpx.AsyncClient, email: str = "fan@example.com") -> httpx.Response:
    return await client.post(
        "/api/v1/auth/register", json={"email": email, "password": "strong-password-123"}
    )


async def login(client: httpx.AsyncClient, email: str = "fan@example.com") -> httpx.Response:
    return await client.post(
        "/api/v1/auth/login", json={"email": email, "password": "strong-password-123"}
    )


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
async def test_login_logout_and_revoked_session_cannot_authenticate(client):
    await register(client)
    assert (await login(client)).status_code == 200
    assert (await client.get("/api/v1/me")).status_code == 200
    assert (await client.post("/api/v1/auth/logout")).status_code == 200
    assert (await client.get("/api/v1/me")).status_code == 401


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
async def test_session_listing_single_revoke_and_revoke_others(client):
    await register(client)
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
