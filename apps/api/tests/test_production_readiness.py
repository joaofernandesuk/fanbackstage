import pytest

from app.core.config import Settings
from app.media import storage
from app.seed.demo import _assert_development


def _production_settings(**overrides) -> Settings:
    values = {
        "environment": "production",
        "session_secret": "s" * 40,
        "notification_webhook_secret": "n" * 40,
        "internal_country_handoff_secret": "i" * 40,
        "payment_webhook_secret": "p" * 40,
        "cookie_secure": True,
        "api_docs_enabled": False,
        "livekit_webhook_configured": True,
        "release_sha": "0123456789abcdef",
        "web_origin": "https://fanbackstage.example",
        "api_origin": "https://api.fanbackstage.example",
        "database_url": (
            "postgresql+asyncpg://fan_prod:database-password-with-32-characters"
            "@db.internal:5432/fanbackstage?ssl=verify-full"
        ),
        "redis_url": "rediss://:redis-password-with-32-characters@redis.internal:6380/0",
        "smtp_host": "smtp.example.com",
        "smtp_username": "fanbackstage-smtp-user",
        "smtp_password": "smtp-password-with-at-least-32-characters",
        "smtp_start_tls": True,
        "storage_endpoint_url": "https://storage.example.com",
        "storage_access_key": "production-storage-access",
        "storage_secret_key": "storage-secret-with-at-least-32-characters",
        "livekit_url": "wss://livekit.example.com",
        "livekit_api_key": "livekit-production-key",
        "livekit_api_secret": "livekit-secret-with-at-least-24-characters",
        "age_assurance_provider": "verifymyage",
        "verifymyage_environment": "production",
        "verifymyage_client_id": "vma-client-id",
        "verifymyage_client_secret": "vma-client-secret-long",
        "compliance_fallback_country": "PT",
        "error_tracking_provider": "sentry",
        "error_tracking_dsn": "https://public-key@errors.example.com/1",
    }
    values.update(overrides)
    return Settings(**values)


def _staging_settings(**overrides) -> Settings:
    values = {
        "environment": "staging",
        "session_secret": "s" * 40,
        "notification_webhook_secret": "n" * 40,
        "internal_country_handoff_secret": "i" * 40,
        "payment_webhook_secret": "p" * 40,
        "cookie_secure": True,
        "api_docs_enabled": False,
        "public_indexing_enabled": False,
        "private_access_gateway_configured": True,
        "livekit_webhook_configured": True,
        "release_sha": "0123456789abcdef",
        "web_origin": "https://staging.fanbackstage.example",
        "api_origin": "https://api.staging.fanbackstage.example",
        "database_url": (
            "postgresql+asyncpg://fan_staging:database-password-with-32-characters"
            "@db.staging.internal:5432/fanbackstage?ssl=verify-full"
        ),
        "redis_url": "rediss://:redis-password-with-32-characters@redis.staging.internal:6380/0",
        "smtp_host": "smtp.staging.example",
        "smtp_username": "fanbackstage-staging",
        "smtp_password": "smtp-password-with-at-least-32-characters",
        "smtp_start_tls": True,
        "email_from": "no-reply@staging.example",
        "storage_endpoint_url": "https://storage.staging.example",
        "storage_public_endpoint_url": "https://media.staging.example",
        "storage_access_key": "staging-storage-access",
        "storage_secret_key": "storage-secret-with-at-least-32-characters",
        "storage_bucket": "fanbackstage-staging-private",
        "livekit_url": "wss://livekit.staging.example",
        "livekit_api_key": "livekit-staging-key",
        "livekit_api_secret": "livekit-secret-with-at-least-24-characters",
        "age_assurance_provider": "verifymyage",
        "verifymyage_environment": "sandbox",
        "kyc_provider": "staging_sandbox",
        "payment_provider": "staging_sandbox",
        "staging_payment_sandbox_environment": "STAGING TEST ONLY",
        "staging_kyc_sandbox_environment": "STAGING TEST ONLY",
        "staging_payment_webhook_secret": "staging-payment-webhook-secret-with-32-characters",
        "staging_kyc_webhook_secret": "staging-kyc-webhook-secret-with-32-characters",
        "trusted_country_header": "X-FanBackstage-Country",
        "trusted_proxy_cidrs": "203.0.113.10/32",
        "compliance_fallback_country": "PT",
        "error_tracking_provider": "sentry",
        "error_tracking_dsn": "https://public-key@errors.staging.example/1",
    }
    values.update(overrides)
    return Settings(**values)


def test_default_media_delivery_budget_supports_media_heavy_public_pages():
    settings = Settings()

    assert settings.media_rate_limit_attempts == 300
    assert settings.media_rate_limit_window_seconds == 60


def test_staging_accepts_shared_environment_configuration_and_reports_capabilities():
    settings = _staging_settings()
    settings.validate_production()
    assert settings.staging_capability_readiness_reasons() == (
        "VERIFYMYAGE_SANDBOX_CONFIGURATION_MISSING",
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"error_tracking_provider": "disabled", "error_tracking_dsn": ""},
        {"error_tracking_provider": "sentry", "error_tracking_dsn": ""},
        {
            "error_tracking_provider": "sentry",
            "error_tracking_dsn": "https://public-key@errors.staging.example/1",
            "error_tracking_send_pii": True,
        },
    ],
)
def test_staging_rejects_missing_or_unsafe_error_exporter(overrides):
    with pytest.raises(RuntimeError, match="ERROR|Sentry|PII"):
        _staging_settings(**overrides).validate_production()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"staging_payment_webhook_secret": ""}, "PAYMENT_SANDBOX_WEBHOOK_CONFIGURATION_MISSING"),
        ({"staging_kyc_webhook_secret": ""}, "CREATOR_KYC_SANDBOX_WEBHOOK_CONFIGURATION_MISSING"),
        ({"staging_payment_sandbox_environment": "wrong"}, "environment marker"),
    ],
)
def test_staging_sandbox_provider_configuration_fails_closed(overrides, message):
    settings = _staging_settings(**overrides)
    if "CONFIGURATION_MISSING" in message:
        assert message in settings.staging_capability_readiness_reasons()
    else:
        with pytest.raises(RuntimeError, match=message):
            settings.validate_production()


@pytest.mark.parametrize("provider_name", ["payment_provider", "kyc_provider"])
def test_production_rejects_staging_only_provider(provider_name):
    settings = _production_settings(**{provider_name: "staging_sandbox"})
    with pytest.raises(RuntimeError, match="staging"):
        settings.validate_production()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"payment_provider": "development"}, "payment provider"),
        ({"kyc_provider": "development"}, "KYC provider"),
        ({"cookie_secure": False}, "secure session cookies"),
        ({"web_origin": "http://localhost:3000"}, "WEB_ORIGIN"),
        ({"api_docs_enabled": True}, "documentation"),
        ({"public_indexing_enabled": True}, "indexing"),
        ({"private_access_gateway_configured": False}, "access gateway"),
        ({"demo_seed_enabled": True}, "demo seed"),
        ({"session_secret": "change-me-for-development-only"}, "SESSION_SECRET"),
        ({"smtp_host": "mailpit"}, "SMTP_HOST"),
        ({"trusted_proxy_cidrs": "0.0.0.0/0"}, "TRUSTED_PROXY_CIDRS"),
    ],
)
def test_staging_rejects_development_shortcuts(overrides, message):
    with pytest.raises(RuntimeError, match=message):
        _staging_settings(**overrides).validate_production()


def test_staging_dataset_and_production_environment_cannot_be_confused():
    with pytest.raises(RuntimeError, match="staging dataset"):
        _production_settings(staging_dataset_enabled=True).validate_production()


def test_production_rejects_unimplemented_payment_provider():
    with pytest.raises(RuntimeError, match="PAYMENT_PROVIDER is not implemented"):
        _production_settings(payment_provider="provider").validate_production()


def test_production_rejects_enabled_demo_seed():
    with pytest.raises(RuntimeError, match="DEMO_SEED"):
        _production_settings(
            payment_provider="provider", demo_seed_enabled=True
        ).validate_production()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("session_secret", "x", "SESSION_SECRET"),
        ("notification_webhook_secret", "x", "NOTIFICATION_WEBHOOK_SECRET"),
        ("payment_webhook_secret", "development-payment-webhook-secret", "PAYMENT_WEBHOOK"),
        ("livekit_api_key", "devkey", "LIVEKIT_API_KEY"),
        ("livekit_api_secret", "x", "LIVEKIT_API_SECRET"),
        ("storage_access_key", "x", "STORAGE_ACCESS_KEY"),
        ("storage_secret_key", "x", "STORAGE_SECRET_KEY"),
        ("verifymyage_client_id", "x", "VERIFYMYAGE_CLIENT_ID"),
        ("verifymyage_client_secret", "x", "VERIFYMYAGE_CLIENT_SECRET"),
        (
            "database_url",
            "postgresql+asyncpg://fanbackstage:fanbackstage@localhost:5432/fanbackstage",
            "DATABASE_URL",
        ),
        ("redis_url", "redis://localhost:6379/0", "REDIS_URL"),
    ],
)
def test_production_rejects_weak_secrets_and_default_stateful_endpoints(field, value, message):
    with pytest.raises(RuntimeError, match=message):
        _production_settings(**{field: value}).validate_production()


@pytest.mark.parametrize(
    "database_url",
    [
        (
            "postgresql+asyncpg://fan_prod:database-password-with-32-characters"
            "@db.internal:5432/fanbackstage"
        ),
        (
            "postgresql+asyncpg://fan_prod:database-password-with-32-characters"
            "@db.internal:5432/fanbackstage?ssl=disable"
        ),
        (
            "postgresql+asyncpg://fan_prod:database-password-with-32-characters"
            "@db.internal:5432/fanbackstage?ssl=allow"
        ),
        (
            "postgresql+asyncpg://fan_prod:database-password-with-32-characters"
            "@db.internal:5432/fanbackstage?ssl=prefer"
        ),
        (
            "postgresql+asyncpg://fan_prod:database-password-with-32-characters"
            "@db.internal:5432/fanbackstage?sslmode=verify-full"
        ),
        (
            "postgresql+asyncpg://fan_prod:database-password-with-32-characters"
            "@db.internal:5432/fanbackstage?ssl=require&ssl=disable"
        ),
        (
            "postgresql://fan_prod:database-password-with-32-characters"
            "@db.internal:5432/fanbackstage?ssl=verify-full"
        ),
    ],
)
def test_production_rejects_missing_fallback_or_incompatible_database_tls(database_url):
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        _production_settings(database_url=database_url).validate_production()


@pytest.mark.parametrize("tls_mode", ["require", "verify-ca", "verify-full"])
def test_production_accepts_explicit_asyncpg_database_tls_modes(tls_mode):
    database_url = (
        "postgresql+asyncpg://fan_prod:database-password-with-32-characters"
        f"@db.internal:5432/fanbackstage?ssl={tls_mode}"
    )
    with pytest.raises(RuntimeError, match="PAYMENT_PROVIDER is not implemented"):
        _production_settings(
            database_url=database_url,
            payment_provider="provider",
        ).validate_production()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("web_origin", "http://fanbackstage.example", "WEB_ORIGIN"),
        ("web_origin", "https://localhost:3000", "WEB_ORIGIN"),
        ("web_origin", "https://user:password@fanbackstage.example", "WEB_ORIGIN"),
        ("web_origin", "https://fanbackstage.example/", "WEB_ORIGIN"),
        ("web_origin", "https://fanbackstage.example?source=test", "WEB_ORIGIN"),
        ("web_origin", "https://fanbackstage.example#fragment", "WEB_ORIGIN"),
        ("api_origin", "https:///api", "API_ORIGIN"),
        ("api_origin", "https://api.fanbackstage.example/v1", "API_ORIGIN"),
        ("api_origin", "https://api.fanbackstage.example:invalid", "API_ORIGIN"),
        ("storage_endpoint_url", "http://storage.example.com", "STORAGE_ENDPOINT_URL"),
        ("storage_endpoint_url", "https://127.0.0.1:9000", "STORAGE_ENDPOINT_URL"),
        (
            "storage_endpoint_url",
            "https://access-key@storage.example.com",
            "STORAGE_ENDPOINT_URL",
        ),
        (
            "storage_endpoint_url",
            "https://storage.example.com?region=eu-west-1",
            "STORAGE_ENDPOINT_URL",
        ),
        (
            "storage_public_endpoint_url",
            "https://cdn.example.com#asset",
            "STORAGE_PUBLIC_ENDPOINT_URL",
        ),
        ("livekit_url", "ws://livekit.example.com", "LIVEKIT_URL"),
        ("livekit_url", "wss://[::1]:7880", "LIVEKIT_URL"),
        ("livekit_url", "wss://token@livekit.example.com", "LIVEKIT_URL"),
        ("livekit_url", "wss://livekit.example.com?token=secret", "LIVEKIT_URL"),
        ("livekit_control_url", "ws://livekit.internal", "LIVEKIT_CONTROL_URL"),
        ("livekit_control_url", "wss://127.0.0.1:7880", "LIVEKIT_CONTROL_URL"),
    ],
)
def test_production_rejects_malformed_or_local_transport_endpoints(field, value, message):
    with pytest.raises(RuntimeError, match=message):
        _production_settings(**{field: value}).validate_production()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"trusted_country_header": "X-Country"}, "configured together"),
        ({"trusted_proxy_cidrs": "203.0.113.10/32"}, "configured together"),
        (
            {
                "trusted_country_header": "X Country",
                "trusted_proxy_cidrs": "203.0.113.10/32",
            },
            "TRUSTED_COUNTRY_HEADER",
        ),
        (
            {
                "trusted_country_header": "X-Country",
                "trusted_proxy_cidrs": "0.0.0.0/0",
            },
            "TRUSTED_PROXY_CIDRS",
        ),
        (
            {
                "trusted_country_header": "X-Country",
                "trusted_proxy_cidrs": "::/0",
            },
            "TRUSTED_PROXY_CIDRS",
        ),
        (
            {
                "trusted_country_header": "X-Country",
                "trusted_proxy_cidrs": "10.0.0.0/8",
            },
            "TRUSTED_PROXY_CIDRS",
        ),
        (
            {
                "trusted_country_header": "X-Country",
                "trusted_proxy_cidrs": "127.0.0.1/32",
            },
            "TRUSTED_PROXY_CIDRS",
        ),
        (
            {
                "trusted_country_header": "X-Country",
                "trusted_proxy_cidrs": "203.0.113.11/24",
            },
            "TRUSTED_PROXY_CIDRS",
        ),
    ],
)
def test_production_rejects_dangerous_trusted_country_proxy_configuration(
    overrides,
    message,
):
    with pytest.raises(RuntimeError, match=message):
        _production_settings(**overrides).validate_production()


def test_production_accepts_narrow_paired_trusted_country_proxy_configuration():
    with pytest.raises(RuntimeError, match="PAYMENT_PROVIDER is not implemented"):
        _production_settings(
            payment_provider="provider",
            trusted_country_header="X-Edge-Country",
            trusted_proxy_cidrs="203.0.113.10/32,2001:db8:abcd::/64",
        ).validate_production()


@pytest.mark.parametrize("ttl", [0, 301])
def test_livekit_tokens_must_remain_short_lived(ttl):
    with pytest.raises(RuntimeError, match="LIVEKIT_TOKEN_TTL_SECONDS"):
        Settings(environment="test", livekit_token_ttl_seconds=ttl).validate_production()


@pytest.mark.parametrize("ttl", [0, 301, 86_400])
def test_protected_media_urls_must_remain_short_lived(ttl):
    with pytest.raises(RuntimeError, match="MEDIA_URL_TTL_SECONDS"):
        Settings(environment="test", media_url_ttl_seconds=ttl).validate_production()


def test_production_accepts_paths_on_non_origin_service_endpoints():
    with pytest.raises(RuntimeError, match="PAYMENT_PROVIDER is not implemented"):
        _production_settings(
            payment_provider="provider",
            storage_endpoint_url="https://storage.example.com/s3",
            storage_public_endpoint_url="https://cdn.example.com/media",
            livekit_url="wss://livekit.example.com/rtc",
        ).validate_production()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"smtp_port": 0}, "SMTP_PORT"),
        ({"smtp_host": "localhost"}, "SMTP_HOST"),
        ({"smtp_host": "[::1]"}, "SMTP_HOST"),
        ({"smtp_host": "mailpit."}, "SMTP_HOST"),
        ({"smtp_host": "mail.internal.local"}, "SMTP_HOST"),
        ({"smtp_start_tls": False}, "requires implicit TLS or forced STARTTLS"),
        ({"smtp_use_tls": True}, "cannot both be true"),
        ({"smtp_username": ""}, "SMTP_USERNAME"),
        ({"smtp_password": ""}, "SMTP_PASSWORD"),
    ],
)
def test_production_rejects_insecure_or_unauthenticated_smtp(overrides, message):
    with pytest.raises(RuntimeError, match=message):
        _production_settings(**overrides).validate_production()


def test_production_accepts_authenticated_implicit_tls_smtp():
    with pytest.raises(RuntimeError, match="PAYMENT_PROVIDER is not implemented"):
        _production_settings(
            payment_provider="provider",
            smtp_start_tls=False,
            smtp_use_tls=True,
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
        Settings(
            environment="staging",
            development_kyc_http_enabled=True,
            error_tracking_provider="sentry",
            error_tracking_dsn="https://public-key@errors.staging.example/1",
            release_sha="abcdef123",
        ).validate_production()


def test_demo_seed_guard_refuses_when_not_explicitly_enabled(monkeypatch):
    monkeypatch.setattr("app.seed.demo.get_settings", lambda: Settings(environment="test"))
    with pytest.raises(RuntimeError, match="Demo seeding requires"):
        _assert_development()


def test_storage_signs_browser_urls_without_exposing_private_network_host(monkeypatch):
    endpoints: list[str] = []
    signed_params: list[dict] = []

    class Client:
        def __init__(self, endpoint: str) -> None:
            self.endpoint = endpoint

        def head_bucket(self, **_kwargs) -> None:
            return None

        def generate_presigned_url(self, _operation: str, **kwargs) -> str:
            signed_params.append(kwargs["Params"])
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
    assert signed_params == [
        {
            "Bucket": "private",
            "Key": "derivative/story",
            "ResponseContentDisposition": "inline",
        }
    ]
