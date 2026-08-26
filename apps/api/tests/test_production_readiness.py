import pytest

from app.core.config import Settings
from app.media import storage
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


def test_storage_signs_browser_urls_without_exposing_private_network_host(monkeypatch):
    endpoints: list[str] = []

    class Client:
        def __init__(self, endpoint: str) -> None:
            self.endpoint = endpoint

        def head_bucket(self, **_kwargs) -> None:
            return None

        def generate_presigned_url(self, _operation: str, **_kwargs) -> str:
            return f"{self.endpoint}/signed"

    def client(_service: str, *, endpoint_url: str, **_kwargs):
        endpoints.append(endpoint_url)
        return Client(endpoint_url)

    monkeypatch.setattr(storage.boto3, "client", client)
    provider = storage.S3StorageProvider(
        endpoint_url="http://minio:9000",
        public_endpoint_url="http://localhost:19010",
        access_key="test",
        secret_key="test",
        bucket="private",
        region="us-east-1",
    )

    assert provider.create_download_url("derivative/story", 60).startswith(
        "http://localhost:19010/"
    )
    assert endpoints == ["http://minio:9000", "http://localhost:19010"]
