"""Baseline 18+ self-attestation and signed anonymous access decisions.

Self-attestation is deliberately distinct from provider-backed identity or age
verification. Jurisdiction-specific assurance belongs behind a future provider
adapter once the applicable rules and provider have been selected.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from starlette.responses import Response

from app.core.config import get_settings
from app.models.identity import User


class AdultAssurance(StrEnum):
    none = "none"
    self_attested = "self_attested"


class AdultAccessSource(StrEnum):
    none = "none"
    account = "account"
    cookie = "cookie"


@dataclass(frozen=True)
class AdultAccessDecision:
    allowed: bool
    assurance: AdultAssurance
    source: AdultAccessSource
    policy_version: str
    expires_at: datetime | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def current_policy_version() -> str:
    return get_settings().adult_attestation_version


def has_current_self_attestation(user: User | None) -> bool:
    return bool(
        user
        and user.adult_attested_at is not None
        and user.adult_attestation_version == current_policy_version()
    )


def attest_account(user: User, *, now: datetime | None = None) -> bool:
    """Persist the current self-attestation version; return whether state changed."""

    if has_current_self_attestation(user):
        return False
    user.adult_attested_at = now or _now()
    user.adult_attestation_version = current_policy_version()
    return True


def require_current_self_attestation(user: User) -> None:
    if not has_current_self_attestation(user):
        raise PermissionError("Current adult self-attestation is required")


def issue_cookie_value(*, now: datetime | None = None) -> tuple[str, datetime]:
    settings = get_settings()
    issued_at = now or _now()
    expires_at = issued_at + timedelta(hours=settings.adult_access_cookie_ttl_hours)
    payload = {
        "assurance": AdultAssurance.self_attested.value,
        "exp": int(expires_at.timestamp()),
        "iat": int(issued_at.timestamp()),
        "policy": settings.adult_attestation_version,
    }
    encoded_payload = _encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signed = f"v1.{encoded_payload}"
    signature = _encode(
        hmac.new(settings.session_secret.encode(), signed.encode(), hashlib.sha256).digest()
    )
    return f"{signed}.{signature}", expires_at


def cookie_decision(value: str | None, *, now: datetime | None = None) -> AdultAccessDecision:
    settings = get_settings()
    denied = AdultAccessDecision(
        allowed=False,
        assurance=AdultAssurance.none,
        source=AdultAccessSource.none,
        policy_version=settings.adult_attestation_version,
    )
    if not value:
        return denied
    try:
        version, encoded_payload, signature = value.split(".", 2)
        if version != "v1":
            return denied
        signed = f"{version}.{encoded_payload}"
        expected = _encode(
            hmac.new(settings.session_secret.encode(), signed.encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(signature, expected):
            return denied
        payload = json.loads(_decode(encoded_payload))
        if set(payload) != {"assurance", "exp", "iat", "policy"}:
            return denied
        if (
            payload["assurance"] != AdultAssurance.self_attested.value
            or payload["policy"] != settings.adult_attestation_version
            or type(payload["iat"]) is not int
            or type(payload["exp"]) is not int
        ):
            return denied
        current = now or _now()
        issued_at = datetime.fromtimestamp(payload["iat"], UTC)
        expires_at = datetime.fromtimestamp(payload["exp"], UTC)
        maximum = timedelta(hours=settings.adult_access_cookie_ttl_hours)
        if issued_at > current + timedelta(minutes=1) or expires_at <= current:
            return denied
        if expires_at <= issued_at or expires_at - issued_at > maximum:
            return denied
        return AdultAccessDecision(
            allowed=True,
            assurance=AdultAssurance.self_attested,
            source=AdultAccessSource.cookie,
            policy_version=settings.adult_attestation_version,
            expires_at=expires_at,
        )
    except (
        binascii.Error,
        OSError,
        OverflowError,
        TypeError,
        UnicodeDecodeError,
        ValueError,
    ):
        return denied


def resolve_adult_access(
    user: User | None, cookie_value: str | None, *, now: datetime | None = None
) -> AdultAccessDecision:
    """Resolve account state first; a cookie cannot elevate an authenticated legacy account."""

    settings = get_settings()
    if user is not None:
        if has_current_self_attestation(user):
            return AdultAccessDecision(
                allowed=True,
                assurance=AdultAssurance.self_attested,
                source=AdultAccessSource.account,
                policy_version=settings.adult_attestation_version,
            )
        return AdultAccessDecision(
            allowed=False,
            assurance=AdultAssurance.none,
            source=AdultAccessSource.none,
            policy_version=settings.adult_attestation_version,
        )
    return cookie_decision(cookie_value, now=now)


def set_adult_access_cookie(response: Response, *, now: datetime | None = None) -> datetime:
    settings = get_settings()
    value, expires_at = issue_cookie_value(now=now)
    response.set_cookie(
        settings.adult_access_cookie_name,
        value,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.adult_access_cookie_ttl_hours * 3600,
        expires=expires_at,
        path="/",
    )
    return expires_at


def restricted_delivery_ttl(
    decision: AdultAccessDecision,
    configured_ttl_seconds: int,
    *,
    now: datetime | None = None,
) -> int:
    """Never let a guest media URL outlive its signed access decision."""

    if configured_ttl_seconds < 1:
        raise ValueError("Restricted media delivery is unavailable")
    if decision.source is not AdultAccessSource.cookie:
        return configured_ttl_seconds
    if decision.expires_at is None:
        raise ValueError("Restricted media delivery is unavailable")
    remaining = int((decision.expires_at - (now or _now())).total_seconds())
    ttl = min(configured_ttl_seconds, remaining)
    if ttl < 1:
        raise ValueError("Restricted media delivery is unavailable")
    return ttl
