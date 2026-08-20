import httpx
import pytest
from sqlalchemy import select, text

from app.accounts import service
from app.main import app
from app.models.audit import AuditEvent
from app.models.identity import User
from app.worker.tasks import ffmpeg_version


@pytest.mark.asyncio
async def test_role_assignment_is_multi_role_and_audited(db_session):
    user, _ = await service.register(
        db_session, "roles@example.com", "strong-password-123", "request-1"
    )
    await db_session.flush()
    await service.assign_role(db_session, user, "creator", user.id, "request-2")
    await db_session.commit()
    await db_session.refresh(user, ["roles"])
    assert {role.name for role in user.roles} == {"viewer", "creator"}
    assert await db_session.get(User, user.id) is not None
    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "role.assigned")
    )
    assert event and event.metadata_json == {"role": "creator"}


@pytest.mark.asyncio
async def test_readiness_rejects_missing_required_dependency(monkeypatch):
    class UnavailableRedis:
        def __init__(self, *args, **kwargs):
            pass

        @classmethod
        def from_url(cls, *args, **kwargs):
            return cls()

        async def ping(self):
            raise ConnectionError("unavailable")

        async def aclose(self):
            pass

    monkeypatch.setattr("app.api.routes.health.Redis", UnavailableRedis)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/ready")
    assert response.status_code == 503


@pytest.mark.asyncio
async def test_cors_preflight_allows_authenticated_subscription_plan_put():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.options(
            "/api/v1/creator/subscription-plan",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "PUT",
                "Access-Control-Request-Headers": "content-type",
            },
        )
    assert response.status_code == 200
    assert "PUT" in response.headers["access-control-allow-methods"]
    assert response.headers["access-control-allow-credentials"] == "true"


@pytest.mark.asyncio
async def test_migrated_schema_has_expected_constraints_indexes_and_foreign_keys(db_session):
    tables = set(
        (
            await db_session.scalars(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        ).all()
    )
    assert {
        "users",
        "roles",
        "user_roles",
        "user_sessions",
        "security_tokens",
        "audit_events",
    } <= tables
    constraints = set(
        (
            await db_session.scalars(
                text(
                    "SELECT conname FROM pg_constraint WHERE connamespace = 'public'::regnamespace"
                )
            )
        ).all()
    )
    assert {
        "ck_users_email_normalized",
        "uq_users_email",
        "uq_roles_name",
        "uq_user_roles_user_id",
    } <= constraints
    indexes = set(
        (
            await db_session.scalars(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = 'public'")
            )
        ).all()
    )
    assert {
        "ix_user_sessions_user_id",
        "ix_security_tokens_expires_at",
        "ix_audit_events_correlation_id",
    } <= indexes
    foreign_keys = set(
        (
            await db_session.scalars(
                text(
                    "SELECT conname FROM pg_constraint WHERE contype = 'f' AND connamespace = 'public'::regnamespace"
                )
            )
        ).all()
    )
    assert {
        "fk_user_sessions_user_id_users",
        "fk_security_tokens_user_id_users",
        "fk_audit_events_actor_user_id_users",
    } <= foreign_keys


def test_ffmpeg_worker_capability_check() -> None:
    result = ffmpeg_version.run()
    assert result["version"].startswith("ffmpeg version")
