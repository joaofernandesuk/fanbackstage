from datetime import UTC, datetime, timedelta

from app.accounts import adult_access
from app.core.config import Settings
from app.models.identity import User


def settings(**overrides) -> Settings:
    return Settings(
        session_secret="adult-access-test-secret",
        adult_access_cookie_ttl_hours=1,
        **overrides,
    )


def test_signed_cookie_rejects_tampering_expiry_and_policy_change(monkeypatch):
    issued_at = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    monkeypatch.setattr(adult_access, "get_settings", settings)
    value, expires_at = adult_access.issue_cookie_value(now=issued_at)
    accepted = adult_access.cookie_decision(value, now=issued_at + timedelta(minutes=30))
    assert accepted.allowed is True
    assert accepted.expires_at == expires_at
    tampered = f"{value[:-1]}{'A' if value[-1] != 'A' else 'B'}"
    assert not adult_access.cookie_decision(tampered, now=issued_at + timedelta(minutes=30)).allowed
    assert not adult_access.cookie_decision(value, now=expires_at).allowed

    monkeypatch.setattr(
        adult_access,
        "get_settings",
        lambda: settings(adult_attestation_version="new-policy"),
    )
    assert not adult_access.cookie_decision(value, now=issued_at + timedelta(minutes=30)).allowed


def test_authenticated_account_cannot_be_elevated_by_guest_cookie(monkeypatch):
    monkeypatch.setattr(adult_access, "get_settings", settings)
    value, _ = adult_access.issue_cookie_value()
    legacy = User(email="legacy@example.com", password_hash="irrelevant")
    assert adult_access.cookie_decision(value).allowed is True
    assert adult_access.resolve_adult_access(legacy, value).allowed is False
    assert adult_access.attest_account(legacy) is True
    decision = adult_access.resolve_adult_access(legacy, None)
    assert decision.allowed is True
    assert decision.source is adult_access.AdultAccessSource.account


def test_guest_restricted_delivery_ttl_never_outlives_attestation():
    now = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    guest = adult_access.AdultAccessDecision(
        allowed=True,
        assurance=adult_access.AdultAssurance.self_attested,
        source=adult_access.AdultAccessSource.cookie,
        policy_version="v1",
        expires_at=now + timedelta(seconds=2, microseconds=900_000),
    )
    assert adult_access.restricted_delivery_ttl(guest, 300, now=now) == 2
    too_late = adult_access.AdultAccessDecision(
        allowed=True,
        assurance=adult_access.AdultAssurance.self_attested,
        source=adult_access.AdultAccessSource.cookie,
        policy_version="v1",
        expires_at=now + timedelta(milliseconds=900),
    )
    try:
        adult_access.restricted_delivery_ttl(too_late, 300, now=now)
    except ValueError as exc:
        assert "unavailable" in str(exc)
    else:
        raise AssertionError("sub-second guest decisions must fail closed")
