from __future__ import annotations

import logging
import secrets
from typing import Any, Protocol

import sentry_sdk

from app.core.config import Settings, get_settings


class ErrorTrackingProvider(Protocol):
    """Adapter boundary for a future hosted or self-managed error tracker."""

    def capture_exception(
        self,
        exception: Exception,
        *,
        event_id: str,
        correlation_id: str | None,
        route: str | None,
        method: str | None,
        status_code: int | None,
        category: str,
        task_name: str | None,
        queue_name: str | None,
    ) -> str | None: ...

    def capture_diagnostic(self, *, event_id: str) -> str | None: ...


class SafeLoggingErrorTracker:
    """Safe local fallback that records no exception message or request payload."""

    def capture_exception(
        self,
        exception: Exception,
        *,
        event_id: str,
        correlation_id: str | None,
        route: str | None,
        method: str | None = None,
        status_code: int | None = None,
        category: str = "api_uncaught_error",
        task_name: str | None = None,
        queue_name: str | None = None,
    ) -> str | None:
        logging.getLogger("fanbackstage.error_tracking").error(
            "unhandled_exception",
            extra={
                "event_id": event_id,
                "correlation_id": correlation_id,
                "route": route,
                "error_type": type(exception).__name__,
                "status_code": status_code,
                "metrics": {
                    "category": category,
                    "method": method,
                    "task": task_name,
                    "queue": queue_name,
                },
            },
        )
        return None

    def capture_diagnostic(self, *, event_id: str) -> str | None:
        logging.getLogger("fanbackstage.error_tracking").info(
            "error_tracking_diagnostic", extra={"event_id": event_id}
        )
        return None


def _scrub_sentry_event(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
    """Retain stack locations and controlled tags, never request or exception values."""

    event.pop("request", None)
    event.pop("user", None)
    event.pop("breadcrumbs", None)
    event.pop("extra", None)
    event.pop("fingerprint", None)
    event.pop("logentry", None)
    event.pop("server_name", None)
    event.pop("spans", None)
    event.pop("transaction", None)
    if "message" in event and event["message"] != "fanbackstage_error_tracking_diagnostic":
        event["message"] = "[redacted]"
    for container_name in ("exception", "threads"):
        container = event.get(container_name)
        if not isinstance(container, dict):
            continue
        for value in container.get("values", []):
            if container_name == "exception":
                value["value"] = "[redacted]"
            frames = value.get("stacktrace", {}).get("frames", [])
            for frame in frames:
                frame.pop("vars", None)
    tags = event.get("tags")
    if isinstance(tags, dict):
        event["tags"] = {
            key: value for key, value in tags.items() if key.startswith("fanbackstage.")
        }
    allowed_contexts = {"browser", "fanbackstage", "os", "runtime"}
    contexts = event.get("contexts", {})
    if not isinstance(contexts, dict):
        contexts = {}
    event["contexts"] = {key: contexts[key] for key in allowed_contexts if key in contexts}
    return event


class SentryErrorTracker:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _scope(self, scope, *, event_id: str, category: str) -> None:
        scope.set_tag("fanbackstage.category", category)
        scope.set_tag("fanbackstage.event_id", event_id)

    def capture_exception(
        self,
        exception: Exception,
        *,
        event_id: str,
        correlation_id: str | None,
        route: str | None,
        method: str | None,
        status_code: int | None,
        category: str,
        task_name: str | None,
        queue_name: str | None,
    ) -> str | None:
        with sentry_sdk.new_scope() as scope:
            self._scope(scope, event_id=event_id, category=category)
            safe_context = {
                "correlation_id": correlation_id,
                "route": route,
                "method": method,
                "status_code": status_code,
                "task_name": task_name,
                "queue_name": queue_name,
            }
            scope.set_context(
                "fanbackstage",
                {
                    "event_id": event_id,
                    "environment": self.settings.environment,
                    "release_sha": self.settings.release_sha,
                    **{key: value for key, value in safe_context.items() if value is not None},
                },
            )
            provider_event_id = sentry_sdk.capture_exception(exception)
            return str(provider_event_id) if provider_event_id else None

    def capture_diagnostic(self, *, event_id: str) -> str | None:
        with sentry_sdk.new_scope() as scope:
            self._scope(scope, event_id=event_id, category="operator_diagnostic")
            scope.set_context(
                "fanbackstage",
                {
                    "event_id": event_id,
                    "environment": self.settings.environment,
                    "release_sha": self.settings.release_sha,
                },
            )
            provider_event_id = sentry_sdk.capture_message(
                "fanbackstage_error_tracking_diagnostic", level="info"
            )
            return str(provider_event_id) if provider_event_id else None


_provider: ErrorTrackingProvider = SafeLoggingErrorTracker()


def install_error_tracking_provider(provider: ErrorTrackingProvider) -> None:
    """Install an adapter during process startup without coupling domain code to a vendor."""

    global _provider
    _provider = provider


def configure_error_tracking(settings: Settings | None = None) -> ErrorTrackingProvider:
    configured = settings or get_settings()
    if configured.error_tracking_provider != "sentry":
        provider: ErrorTrackingProvider = SafeLoggingErrorTracker()
        install_error_tracking_provider(provider)
        return provider
    sentry_sdk.init(
        dsn=configured.error_tracking_dsn,
        environment=configured.environment,
        release=configured.release_sha,
        send_default_pii=False,
        default_integrations=False,
        auto_enabling_integrations=False,
        include_local_variables=False,
        max_breadcrumbs=0,
        traces_sample_rate=0.0,
        before_send=_scrub_sentry_event,
    )
    provider = SentryErrorTracker(configured)
    install_error_tracking_provider(provider)
    return provider


def capture_exception(
    exception: Exception,
    *,
    correlation_id: str | None,
    route: str | None,
    method: str | None = None,
    status_code: int | None = None,
    category: str = "api_uncaught_error",
    task_name: str | None = None,
    queue_name: str | None = None,
) -> str:
    event_id = secrets.token_hex(16)
    try:
        provider_event_id = _provider.capture_exception(
            exception,
            event_id=event_id,
            correlation_id=correlation_id,
            route=route,
            method=method,
            status_code=status_code,
            category=category,
            task_name=task_name,
            queue_name=queue_name,
        )
    except Exception:  # noqa: BLE001 - telemetry must never change domain outcomes
        SafeLoggingErrorTracker().capture_exception(
            exception,
            event_id=event_id,
            correlation_id=correlation_id,
            route=route,
            method=method,
            status_code=status_code,
            category=category,
            task_name=task_name,
            queue_name=queue_name,
        )
        provider_event_id = None
    return provider_event_id or event_id


def capture_diagnostic() -> str:
    event_id = secrets.token_hex(16)
    try:
        return _provider.capture_diagnostic(event_id=event_id) or event_id
    except Exception:  # noqa: BLE001 - the generated ID lets operators diagnose delivery
        SafeLoggingErrorTracker().capture_diagnostic(event_id=event_id)
        return event_id
