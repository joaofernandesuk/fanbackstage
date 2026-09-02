from __future__ import annotations

import logging
import secrets
from typing import Protocol


class ErrorTrackingProvider(Protocol):
    """Adapter boundary for a future hosted or self-managed error tracker."""

    def capture_exception(
        self,
        exception: Exception,
        *,
        event_id: str,
        correlation_id: str | None,
        route: str | None,
    ) -> None: ...


class SafeLoggingErrorTracker:
    """Safe local fallback that records no exception message or request payload."""

    def capture_exception(
        self,
        exception: Exception,
        *,
        event_id: str,
        correlation_id: str | None,
        route: str | None,
    ) -> None:
        logging.getLogger("fanbackstage.error_tracking").error(
            "unhandled_exception",
            extra={
                "event_id": event_id,
                "correlation_id": correlation_id,
                "route": route,
                "error_type": type(exception).__name__,
            },
        )


_provider: ErrorTrackingProvider = SafeLoggingErrorTracker()


def install_error_tracking_provider(provider: ErrorTrackingProvider) -> None:
    """Install an adapter during process startup without coupling domain code to a vendor."""

    global _provider
    _provider = provider


def capture_exception(
    exception: Exception,
    *,
    correlation_id: str | None,
    route: str | None,
) -> str:
    event_id = secrets.token_hex(16)
    _provider.capture_exception(
        exception,
        event_id=event_id,
        correlation_id=correlation_id,
        route=route,
    )
    return event_id
