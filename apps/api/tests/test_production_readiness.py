import pytest

from app.core.config import Settings
from app.media import storage
from app.seed.demo import _assert_development


def test_production_rejects_unimplemented_payment_provider():
    with pytest.raises(RuntimeError, match="PAYMENT_PROVIDER is not implemented"):
        Settings(
            environment="production",
            session_secret="safe",
            cookie_secure=True,
            web_origin="https://fanbackstage.com",
            payment_provider="provider",
            notification_webhook_secret="safe",
            livekit_api_secret="safe",
            smtp_host="smtp.example.com",
            storage_endpoint_url="https://storage.example.com",
            storage_access_key="safe",
            storage_secret_key="safe",
        ).validate_production()


def test_production_rejects_enabled_demo_seed():
    with pytest.raises(RuntimeError, match="DEMO_SEED"):
        Settings(
            environment="production",
            session_secret="safe",
            cookie_secure=True,
            web_origin="https://fanbackstage.com",
            payment_provider="provider",
            notification_webhook_secret="safe",
            livekit_api_secret="safe",
            smtp_host="smtp.example.com",
            storage_endpoint_url="https://storage.example.com",
            storage_access_key="safe",
            storage_secret_key="safe",
            demo_seed_enabled=True,
        ).validate_production()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("age_assurance_provider", "fictional-provider", "AGE_ASSURANCE_PROVIDER"),
        ("kyc_provider", "fictional-provider", "KYC_PROVIDER"),
        ("payment_provider", "fictional-provider", "PAYMENT_PROVIDER"),
    ],
)
def test_unimplemented_identity_providers_are_rejected(field, value, message):
    with pytest.raises(RuntimeError, match=message):
        Settings(environment="test", **{field: value}).validate_production()


def test_development_kyc_http_opt_in_is_forbidden_outside_dev_and_test():
    with pytest.raises(RuntimeError, match="limited to development and test"):
        Settings(environment="staging", development_kyc_http_enabled=True).validate_production()


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
