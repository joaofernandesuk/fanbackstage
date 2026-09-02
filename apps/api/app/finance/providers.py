"""Provider-neutral payment checkout and verified callback boundary."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.core.config import get_settings
from app.models.finance import PaymentAttempt


class PaymentProviderError(ValueError):
    pass


@dataclass(frozen=True)
class PaymentEvent:
    external_event_id: str
    event_type: str
    payment_reference: str


@dataclass(frozen=True)
class PaymentCheckout:
    provider_reference: str
    action: str
    expires_at: datetime | None = None


class PaymentProvider(Protocol):
    name: str

    def create_checkout(self, attempt: PaymentAttempt) -> PaymentCheckout: ...

    def verify_webhook(self, payload: bytes, signature: str | None) -> PaymentEvent: ...

    def signed_event(
        self, attempt: PaymentAttempt, event_type: str, event_id: str
    ) -> tuple[bytes, str]: ...


def _signed_event(
    *, secret: str, attempt: PaymentAttempt, event_type: str, event_id: str
) -> tuple[bytes, str]:
    payload = json.dumps(
        {"id": event_id, "type": event_type, "payment_reference": attempt.provider_reference},
        separators=(",", ":"),
    ).encode()
    return payload, hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()


def _verify_event(*, secret: str, payload: bytes, signature: str | None) -> PaymentEvent:
    expected = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(expected, signature):
        raise PaymentProviderError("Invalid payment webhook signature")
    try:
        event = json.loads(payload)
        if (
            not isinstance(event, dict)
            or not all(
                isinstance(event.get(field), str) for field in ("id", "type", "payment_reference")
            )
            or any(len(event[field]) > 255 for field in ("id", "type", "payment_reference"))
        ):
            raise ValueError
    except (ValueError, json.JSONDecodeError) as exc:
        raise PaymentProviderError("Invalid payment webhook payload") from exc
    return PaymentEvent(event["id"], event["type"], event["payment_reference"])


class DevelopmentPaymentProvider:
    name = "development"

    def create_checkout(self, attempt: PaymentAttempt) -> PaymentCheckout:
        return PaymentCheckout(attempt.provider_reference, "development_complete")

    def payment_succeeded_payload(self, attempt: PaymentAttempt) -> tuple[bytes, str]:
        return self.signed_event(attempt, "payment.succeeded", f"dev_event_{attempt.id}")

    def signed_event(
        self, attempt: PaymentAttempt, event_type: str, event_id: str
    ) -> tuple[bytes, str]:
        return _signed_event(
            secret=get_settings().payment_webhook_secret,
            attempt=attempt,
            event_type=event_type,
            event_id=event_id,
        )

    def verify_webhook(self, payload: bytes, signature: str | None) -> PaymentEvent:
        return _verify_event(
            secret=get_settings().payment_webhook_secret, payload=payload, signature=signature
        )


class StagingPaymentProvider:
    """Fictional asynchronous processor used exclusively in staging/test."""

    name = "staging_sandbox"

    def __init__(self) -> None:
        settings = get_settings()
        if settings.environment not in {"staging", "test"}:
            raise PaymentProviderError("Staging payment sandbox is unavailable")
        if not settings.staging_payment_webhook_secret:
            raise PaymentProviderError("Staging payment sandbox is not configured")

    def create_checkout(self, attempt: PaymentAttempt) -> PaymentCheckout:
        return PaymentCheckout(attempt.provider_reference, "staging_sandbox_checkout")

    def signed_event(
        self, attempt: PaymentAttempt, event_type: str, event_id: str
    ) -> tuple[bytes, str]:
        return _signed_event(
            secret=get_settings().staging_payment_webhook_secret,
            attempt=attempt,
            event_type=event_type,
            event_id=event_id,
        )

    def verify_webhook(self, payload: bytes, signature: str | None) -> PaymentEvent:
        return _verify_event(
            secret=get_settings().staging_payment_webhook_secret,
            payload=payload,
            signature=signature,
        )


def payment_provider() -> PaymentProvider:
    provider = get_settings().payment_provider
    if provider == "development":
        return DevelopmentPaymentProvider()
    if provider == "staging_sandbox":
        return StagingPaymentProvider()
    raise PaymentProviderError("Configured payment provider is unavailable")


def new_provider_reference() -> str:
    """Opaque provider-side checkout reference; never a payment instrument."""
    prefix = "stgpay" if get_settings().payment_provider == "staging_sandbox" else "devpay"
    return f"{prefix}_{secrets.token_urlsafe(18)}"
