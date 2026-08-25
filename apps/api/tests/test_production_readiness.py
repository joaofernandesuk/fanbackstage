import pytest

from app.core.config import Settings
from app.seed.demo import _assert_development


def test_production_rejects_local_dependencies_and_demo_seed():
    with pytest.raises(RuntimeError, match="development KYC"):
        Settings(
            environment="production",
            session_secret="safe",
            cookie_secure=True,
            web_origin="https://fanbackstage.com",
        ).validate_production()


def test_production_rejects_enabled_demo_seed():
    with pytest.raises(RuntimeError, match="DEMO_SEED"):
        Settings(
            environment="production",
            session_secret="safe",
            cookie_secure=True,
            web_origin="https://fanbackstage.com",
            kyc_provider="provider",
            payment_provider="provider",
            notification_webhook_secret="safe",
            livekit_api_secret="safe",
            smtp_host="smtp.example.com",
            storage_endpoint_url="https://storage.example.com",
            storage_access_key="safe",
            storage_secret_key="safe",
            demo_seed_enabled=True,
        ).validate_production()


def test_demo_seed_guard_refuses_when_not_explicitly_enabled(monkeypatch):
    monkeypatch.setattr("app.seed.demo.get_settings", lambda: Settings(environment="test"))
    with pytest.raises(RuntimeError, match="Demo seeding requires"):
        _assert_development()
