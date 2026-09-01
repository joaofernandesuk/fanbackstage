"""Provider boundary for payment creation and verified event parsing."""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
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


class PaymentProvider(Protocol):
    name: str

    def verify_webhook(self, payload: bytes, signature: str | None) -> PaymentEvent: ...


class DevelopmentPaymentProvider:
    name = "development"

    def payment_succeeded_payload(self, attempt: PaymentAttempt) -> tuple[bytes, str]:
        payload = json.dumps(
            {
                "id": f"dev_event_{attempt.id}",
                "type": "payment.succeeded",
                "payment_reference": attempt.provider_reference,
            },
            separators=(",", ":"),
        ).encode()
        signature = hmac.new(
            get_settings().payment_webhook_secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        return payload, signature

    def verify_webhook(self, payload: bytes, signature: str | None) -> PaymentEvent:
        expected = hmac.new(
            get_settings().payment_webhook_secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        if not signature or not hmac.compare_digest(expected, signature):
            raise PaymentProviderError("Invalid payment webhook signature")
        try:
            event = json.loads(payload)
            if not all(
                isinstance(event.get(field), str) for field in ("id", "type", "payment_reference")
            ):
                raise ValueError
        except (ValueError, json.JSONDecodeError) as exc:
            raise PaymentProviderError("Invalid payment webhook payload") from exc
        return PaymentEvent(event["id"], event["type"], event["payment_reference"])


def payment_provider() -> PaymentProvider:
    if get_settings().payment_provider == "development":
        return DevelopmentPaymentProvider()
    raise PaymentProviderError("Configured payment provider is unavailable")


def new_provider_reference() -> str:
    """Create an opaque development-provider checkout reference."""

    return f"devpay_{secrets.token_urlsafe(18)}"
