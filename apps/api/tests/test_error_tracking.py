import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request
from sqlalchemy import select

from app.accounts import service as accounts
from app.api.routes import admin as admin_routes
from app.core.config import Settings
from app.main import _error_category, unhandled_exception
from app.models.audit import AuditEvent
from app.observability import errors
from app.worker import celery_app


class RecordingTracker:
    def __init__(self) -> None:
        self.exceptions: list[dict] = []
        self.diagnostics: list[str] = []

    def capture_exception(self, exception, **context):
        self.exceptions.append({"exception": exception, **context})
        return "provider-event-id"

    def capture_diagnostic(self, *, event_id: str):
        self.diagnostics.append(event_id)
        return "provider-diagnostic-id"


@pytest.fixture(autouse=True)
def reset_error_tracker():
    yield
    errors.install_error_tracking_provider(errors.SafeLoggingErrorTracker())


def test_disabled_exporter_is_a_network_free_logging_fallback(monkeypatch):
    init = monkeypatch.setattr(errors.sentry_sdk, "init", lambda **_kwargs: pytest.fail("SDK init"))
    provider = errors.configure_error_tracking(Settings(environment="test"))
    assert isinstance(provider, errors.SafeLoggingErrorTracker)
    assert init is None


def test_exporter_failure_cannot_change_application_outcome(caplog):
    class FailingTracker(RecordingTracker):
        def capture_exception(self, exception, **context):
            raise ConnectionError("private exporter failure")

    errors.install_error_tracking_provider(FailingTracker())
    event_id = errors.capture_exception(
        RuntimeError("private application value"),
        correlation_id="request-123",
        route="/api/v1/example",
    )
    assert len(event_id) == 32
    assert "private exporter failure" not in caplog.text
    assert "private application value" not in caplog.text


def test_sentry_configuration_disables_pii_and_attaches_release(monkeypatch):
    captured = {}
    monkeypatch.setattr(errors.sentry_sdk, "init", lambda **kwargs: captured.update(kwargs))
    provider = errors.configure_error_tracking(
        Settings(
            environment="staging",
            error_tracking_provider="sentry",
            error_tracking_dsn="https://public-key@errors.example.invalid/1",
            release_sha="abcdef123456",
        )
    )
    assert isinstance(provider, errors.SentryErrorTracker)
    assert captured["environment"] == "staging"
    assert captured["release"] == "abcdef123456"
    assert captured["send_default_pii"] is False
    assert captured["max_breadcrumbs"] == 0
    assert captured["default_integrations"] is False


def test_sentry_event_scrubber_drops_sensitive_request_material():
    event = errors._scrub_sentry_event(
        {
            "breadcrumbs": [{"message": "private message"}],
            "extra": {"provider_payload": "secret"},
            "message": "callback?code=secret",
            "request": {
                "cookies": {"session": "secret"},
                "headers": {"authorization": "Bearer secret"},
                "query_string": "token=secret",
            },
            "tags": {"fanbackstage.category": "api_uncaught_error", "private": "secret"},
            "threads": {"values": [{"stacktrace": {"frames": [{"vars": {"token": "secret"}}]}}]},
            "user": {"email": "person@example.com"},
            "exception": {
                "values": [
                    {
                        "value": "private token",
                        "stacktrace": {"frames": [{"vars": {"password": "secret"}}]},
                    }
                ]
            },
            "contexts": {
                "fanbackstage": {"route": "/payments/webhooks/{provider}"},
                "response": {"body": "private"},
            },
        },
        {},
    )
    encoded = json.dumps(event)
    assert event["message"] == "[redacted]"
    assert set(event["contexts"]) == {"fanbackstage"}
    assert event["tags"] == {"fanbackstage.category": "api_uncaught_error"}
    assert not any(
        word in encoded
        for word in (
            "authorization",
            "query_string",
            "person@example.com",
            "password",
            "private token",
        )
    )


def test_sentry_adapter_attaches_only_controlled_request_context(monkeypatch):
    class Scope:
        def __init__(self):
            self.tags = {}
            self.contexts = {}

        def set_tag(self, key, value):
            self.tags[key] = value

        def set_context(self, key, value):
            self.contexts[key] = value

    scope = Scope()

    @contextmanager
    def new_scope():
        yield scope

    monkeypatch.setattr(errors.sentry_sdk, "new_scope", new_scope)
    monkeypatch.setattr(errors.sentry_sdk, "capture_exception", lambda _exception: "sentry-id")
    tracker = errors.SentryErrorTracker(Settings(environment="staging", release_sha="abcdef123"))
    assert (
        tracker.capture_exception(
            RuntimeError("private"),
            event_id="safe-id",
            correlation_id="request-123",
            route="/api/v1/example/{item_id}",
            method="POST",
            status_code=500,
            category="api_uncaught_error",
            task_name=None,
            queue_name=None,
        )
        == "sentry-id"
    )
    assert scope.contexts == {
        "fanbackstage": {
            "event_id": "safe-id",
            "environment": "staging",
            "release_sha": "abcdef123",
            "correlation_id": "request-123",
            "route": "/api/v1/example/{item_id}",
            "method": "POST",
            "status_code": 500,
        }
    }


@pytest.mark.parametrize(
    ("route", "category"),
    [
        ("/api/v1/example", "api_uncaught_error"),
        ("/api/v1/payments/webhooks/staging-sandbox", "payment_callback_failure"),
        ("/api/v1/creators/webhooks/staging-sandbox", "kyc_callback_failure"),
        ("/api/v1/compliance/age-verification/callback/verifymyage", "kyc_callback_failure"),
        ("/api/v1/live/webhooks/livekit", "livekit_control_failure"),
    ],
)
def test_uncaught_api_categories_are_alertable_without_request_data(route, category):
    assert _error_category(route) == category


@pytest.mark.asyncio
async def test_api_uncaught_exception_is_captured_exactly_once(monkeypatch):
    captured = []
    monkeypatch.setattr(
        "app.main.capture_exception",
        lambda exception, **kwargs: captured.append((exception, kwargs)) or "event-id",
    )
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/payments/webhooks/staging-sandbox",
            "headers": [],
            "query_string": b"token=must-not-be-captured",
            "route": SimpleNamespace(path="/api/v1/payments/webhooks/staging-sandbox"),
        }
    )
    request.state.correlation_id = "request-123"
    response = await unhandled_exception(request, RuntimeError("private payload"))
    assert len(captured) == 1
    assert captured[0][1] == {
        "correlation_id": "request-123",
        "route": "/api/v1/payments/webhooks/staging-sandbox",
        "method": "POST",
        "status_code": 500,
        "category": "payment_callback_failure",
    }
    assert json.loads(response.body) == {"detail": "Internal server error", "event_id": "event-id"}


def test_celery_task_failure_is_captured_with_safe_queue_context(monkeypatch):
    captured = []
    monkeypatch.setattr(
        celery_app,
        "capture_exception",
        lambda exception, **kwargs: captured.append((exception, kwargs)) or "event-id",
    )
    sender = SimpleNamespace(
        name="app.worker.tasks.process_media",
        request=SimpleNamespace(delivery_info={"routing_key": "media"}),
    )
    celery_app.log_task_failure(
        sender=sender, task_id="private-task-id", exception=RuntimeError("private")
    )
    assert len(captured) == 1
    assert captured[0][1]["category"] == "media_processing_failure"
    assert captured[0][1]["task_name"] == "app.worker.tasks.process_media"
    assert captured[0][1]["queue_name"] == "media"
    assert "private-task-id" not in str(captured)


@pytest.mark.asyncio
async def test_diagnostic_is_admin_only_and_audits_safe_fields(monkeypatch, db_session):
    viewer, _ = await accounts.register(
        db_session, "diagnostic-viewer@example.com", "strong-password-123", None
    )
    admin, _ = await accounts.register(
        db_session, "diagnostic-admin@example.com", "strong-password-123", None
    )
    await accounts.assign_role(db_session, admin, "admin", admin.id, "test")
    await db_session.flush()
    await db_session.refresh(admin, ["roles"])
    with pytest.raises(HTTPException) as denied:
        await admin_routes.error_tracking_diagnostic((viewer, None), db_session)
    assert denied.value.status_code == 403

    tracker = RecordingTracker()
    errors.install_error_tracking_provider(tracker)
    monkeypatch.setattr(
        admin_routes,
        "get_settings",
        lambda: Settings(
            environment="staging",
            error_tracking_provider="sentry",
            release_sha="abcdef123",
        ),
    )
    result = await admin_routes.error_tracking_diagnostic((admin, None), db_session)
    assert result == {"event_id": "provider-diagnostic-id", "status": "queued"}
    event = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == "operations.error_tracking_diagnostic_requested"
        )
    )
    assert event is not None
    assert event.target_id == "provider-diagnostic-id"
    assert event.metadata_json == {"provider": "sentry", "release_sha": "abcdef123"}
    assert tracker.diagnostics and "diagnostic-admin@example.com" not in json.dumps(
        event.metadata_json
    )
