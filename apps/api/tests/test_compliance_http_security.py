import logging
import time
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from hmac import new as hmac_new
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import Request
from sqlalchemy import func, select

from app.accounts import service as account_service
from app.accounts.service import ensure_roles
from app.compliance import http as compliance_http
from app.compliance.age_verification import start_age_verification
from app.compliance.http import resolve_request_jurisdiction_with_evidence
from app.compliance.policy import create_feature_flag_revision
from app.compliance.types import JurisdictionSignals
from app.core.config import Settings, get_settings
from app.integrations.age_verification.verifymyage import VerifyMyAgeProvider
from app.main import app
from app.models.audit import AuditEvent
from app.models.compliance import ComplianceFeature
from app.models.creator import CreatorProfile
from app.models.identity import Role, User


async def _authenticated_user(db, *, email: str, role_name: str) -> tuple[User, str]:
    await ensure_roles(db)
    await db.flush()
    role = await db.scalar(select(Role).where(Role.name == role_name))
    assert role is not None
    user = User(
        email=email,
        password_hash="not-authenticatable",
        email_verified_at=datetime.now(UTC),
        country_code="PT",
        roles=[role],
    )
    db.add(user)
    await db.flush()
    session = await account_service.create_session(db, user, "compliance-http-test", "test-client")
    return user, session


async def test_platform_shutdown_blocks_product_mutation_but_preserves_narrow_recovery(
    db_session,
):
    actor = await db_session.scalar(
        select(User).where(User.email == "compliance-policy-fixture@example.test")
    )
    assert actor is not None
    await create_feature_flag_revision(
        db_session,
        feature=ComplianceFeature.platform_access,
        country_scope="PT",
        enabled=False,
        effective_from=datetime.now(UTC) - timedelta(seconds=1),
        effective_until=None,
        actor_user_id=actor.id,
        change_reason="Exercise the authenticated maintenance boundary",
    )
    fan, fan_session = await _authenticated_user(
        db_session,
        email="maintenance-fan@example.com",
        role_name="viewer",
    )
    admin, admin_session = await _authenticated_user(
        db_session,
        email="maintenance-admin@example.com",
        role_name="super_admin",
    )
    await db_session.commit()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as fan_client:
        fan_client.headers["User-Agent"] = "fanbackstage-compliance-recovery-test"
        fan_client.cookies.set(get_settings().session_cookie_name, fan_session)
        blocked = await fan_client.post("/api/v1/creators/me/application")
        assert blocked.status_code == 403
        assert blocked.json()["detail"]["code"] == "FEATURE_UNAVAILABLE"
        assert blocked.json()["detail"]["action"] == "RETRY_LATER"
        assert await db_session.scalar(select(func.count(CreatorProfile.id))) == 0

        assert (await fan_client.get("/api/v1/me")).status_code == 200
        assert (await fan_client.get("/api/v1/sessions")).status_code == 200
        assert (await fan_client.get("/api/v1/notifications/preferences")).status_code == 200
        assert (await fan_client.post("/api/v1/notifications/unsubscribe")).status_code == 200
        assert (
            await fan_client.post("/api/v1/auth/adult-access", json={"adult_confirmed": True})
        ).status_code == 200
        forbidden_recovery = await fan_client.get("/api/v1/admin/compliance/templates")
        assert forbidden_recovery.status_code == 403
        assert (await fan_client.post("/api/v1/auth/logout")).status_code == 200

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as admin_client:
        admin_client.cookies.set(get_settings().session_cookie_name, admin_session)
        operator_recovery = await admin_client.get("/api/v1/admin/compliance/templates")
        assert operator_recovery.status_code == 200
        admin_client.headers["X-Request-ID"] = "provider-probe-audit-correlation"
        admin_client.headers["User-Agent"] = "fanbackstage-compliance-admin-audit-test"
        probe = await admin_client.post(
            "/api/v1/admin/compliance/providers/probe",
            json={"provider": "test"},
        )
        assert probe.status_code == 200
        audit_response = await admin_client.get(
            "/api/v1/admin/compliance/audit",
            params={"search": "compliance.provider_probe_completed"},
        )
        assert audit_response.status_code == 200
        audit_items = audit_response.json()["items"]
        assert len(audit_items) == 1
        probe_audit = audit_items[0]
        assert probe_audit["actor_user_id"] == str(admin.id)
        assert probe_audit["target_id"] == probe.json()["id"]
        assert probe_audit["correlation_id"] == "provider-probe-audit-correlation"
        assert probe_audit["ip_address"] == "127.0.0.1"
        assert probe_audit["user_agent"] == "fanbackstage-compliance-admin-audit-test"
        assert probe_audit["metadata"]["provider"] == "test"

    await db_session.refresh(fan)
    assert fan.adult_attested_at is not None
    attestation_event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "account.adult_attested")
    )
    assert attestation_event is not None
    assert attestation_event.ip_address == "127.0.0.1"
    assert attestation_event.user_agent == "fanbackstage-compliance-recovery-test"


async def test_age_callback_is_non_cacheable_and_launchers_disable_query_access_logs(
    db_session,
):
    started = await start_age_verification(db_session, user=None, country_code="PT")
    state = parse_qs(urlsplit(started.authorization_url).query)["state"][0]
    await db_session.commit()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as client:
        response = await client.get(
            "/api/v1/compliance/age-verification/callback/test",
            params={"state": state, "code": "approved"},
        )
    assert response.status_code == 303
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert state not in response.headers["location"]
    assert "approved" not in response.headers["location"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", follow_redirects=False
    ) as client:
        rejected = await client.get(
            "/api/v1/compliance/age-verification/callback/test",
            params={"state": "invalid-transient-state", "code": "invalid-transient-code"},
        )
    assert rejected.status_code == 400
    assert rejected.headers["cache-control"] == "no-store"
    assert rejected.headers["referrer-policy"] == "no-referrer"

    repository = Path(__file__).resolve().parents[3]
    launchers = (
        repository / "docker-compose.dev.yml",
        repository / "Makefile",
        repository / "apps/web/scripts/start-e2e-api.sh",
    )
    for launcher in launchers:
        launcher_text = launcher.read_text()
        assert "--no-access-log" in launcher_text
        assert "--no-proxy-headers" in launcher_text


async def test_selected_country_cannot_silently_override_account_country(db_session):
    user = User(
        email="selected-country-conflict@example.test",
        password_hash="not-authenticatable",
        country_code="PT",
    )
    db_session.add(user)
    await db_session.flush()
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "client": None})

    assert (
        await resolve_request_jurisdiction_with_evidence(
            db_session,
            request,
            user=user,
            signals=JurisdictionSignals(selected_country="GB"),
        )
        is None
    )


def test_internal_legal_ssr_country_handoff_is_signed_fresh_and_path_bound(monkeypatch):
    secret = "internal-country-handoff-test-secret-123456"
    settings = Settings(
        environment="production",
        internal_country_handoff_secret=secret,
        compliance_fallback_country="PT",
    )
    monkeypatch.setattr(compliance_http, "get_settings", lambda: settings)
    timestamp = int(time.time())
    path = "/api/v1/legal/documents"
    signature = hmac_new(secret.encode(), f"GB\n{timestamp}\n{path}".encode(), sha256).hexdigest()

    def request_for(candidate_signature: str, candidate_timestamp: int = timestamp) -> Request:
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": path,
                "query_string": b"",
                "headers": [
                    (b"x-fanbackstage-internal-country", b"GB"),
                    (
                        b"x-fanbackstage-internal-country-timestamp",
                        str(candidate_timestamp).encode(),
                    ),
                    (
                        b"x-fanbackstage-internal-country-signature",
                        candidate_signature.encode(),
                    ),
                ],
                "client": ("127.0.0.1", 50000),
                "server": ("api.test", 443),
                "scheme": "https",
            }
        )

    trusted = compliance_http.jurisdiction_signals_from_request(request_for(signature), user=None)
    assert trusted.trusted_proxy_country == "GB"
    forged = compliance_http.jurisdiction_signals_from_request(request_for("0" * 64), user=None)
    assert forged.trusted_proxy_country is None
    stale = compliance_http.jurisdiction_signals_from_request(
        request_for(signature, timestamp - 120), user=None
    )
    assert stale.trusted_proxy_country is None


async def test_verifymyage_query_token_is_absent_from_http_client_logs(caplog):
    sentinel = "transient-token-must-never-be-logged"

    def provider_response(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": sentinel})
        assert request.url.path == "/users/me"
        assert request.url.params["access_token"] == sentinel
        return httpx.Response(
            200,
            json={"id": "safe-provider-reference", "age_verified": True, "threshold": 18},
        )

    caplog.set_level(logging.INFO)
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider_response)) as client:
        provider = VerifyMyAgeProvider(
            environment="sandbox",
            client_id="safe-client-id",
            client_secret="safe-client-secret",
            client=client,
        )
        result = await provider.exchange_browser_callback("safe-callback-code")

    assert result.age_verified is True
    assert sentinel not in caplog.text


async def test_unauthenticated_webhooks_are_bounded_before_parsing(db_session):
    oversized_livekit = b"x" * (64 * 1024 + 1)
    oversized_notification = b"x" * (16 * 1024 + 1)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        livekit = await client.post(
            "/api/v1/live/webhooks/livekit",
            content=oversized_livekit,
            headers={"Authorization": "untrusted"},
        )
        assert livekit.status_code == 413

        # The notification shared secret is checked before any JSON parsing or
        # body aggregation. A caller without it receives 401 regardless of size.
        unauthenticated = await client.post(
            "/api/v1/notifications/provider-events",
            content=oversized_notification,
        )
        assert unauthenticated.status_code == 401
        authenticated = await client.post(
            "/api/v1/notifications/provider-events",
            content=oversized_notification,
            headers={
                "X-FanBackstage-Provider-Secret": (get_settings().notification_webhook_secret)
            },
        )
        assert authenticated.status_code == 413
