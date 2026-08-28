import re
from functools import lru_cache
from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

IMPLEMENTED_AGE_ASSURANCE_PROVIDERS = {
    "development_self_attestation",
    "test",
    "verifymyage",
}
IMPLEMENTED_KYC_PROVIDERS = {"development"}
IMPLEMENTED_PAYMENT_PROVIDERS = {"development"}
PRODUCTION_POSTGRES_TLS_MODES = frozenset({"require", "verify-ca", "verify-full"})
HTTP_HEADER_NAME = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,64}$")

UNSAFE_PRODUCTION_VALUES = frozenset(
    {
        "",
        "safe",
        "secret",
        "password",
        "changeme",
        "change-me",
        "test",
    }
)


def _require_production_value(
    name: str,
    value: str,
    *,
    minimum_length: int,
    forbidden: set[str] | frozenset[str] = frozenset(),
) -> None:
    normalized = value.strip()
    if (
        len(normalized) < minimum_length
        or normalized.lower() in UNSAFE_PRODUCTION_VALUES
        or normalized in forbidden
    ):
        raise RuntimeError(f"{name} is unsafe for production")


def _is_local_host(hostname: str) -> bool:
    normalized = hostname.rstrip(".").lower()
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    if normalized.endswith(".local"):
        return True
    try:
        address = ip_address(normalized)
    except ValueError:
        return False
    if address.version == 6 and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return address.is_loopback or address.is_unspecified or address.is_link_local


def _require_production_endpoint(
    name: str,
    value: str,
    *,
    scheme: str,
    origin: bool = False,
) -> None:
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise RuntimeError(f"{name} is invalid for production") from exc
    host_and_port = parsed.netloc.rsplit("@", 1)[-1]
    if (
        value != value.strip()
        or any(character.isspace() for character in value)
        or "\\" in value
        or parsed.scheme != scheme
        or not parsed.netloc
        or not hostname
        or _is_local_host(hostname)
        or parsed.username is not None
        or parsed.password is not None
        or host_and_port.endswith(":")
        or "?" in value
        or "#" in value
        or (origin and bool(parsed.path))
    ):
        raise RuntimeError(f"{name} is invalid for production")


def _validate_production_country_proxy(header_name: str, cidr_list: str) -> None:
    header = header_name.strip()
    cidrs = [value.strip() for value in cidr_list.split(",") if value.strip()]
    if bool(header) != bool(cidrs):
        raise RuntimeError(
            "FANBACKSTAGE_TRUSTED_COUNTRY_HEADER and "
            "FANBACKSTAGE_TRUSTED_PROXY_CIDRS must be configured together"
        )
    if not header:
        return
    if header != header_name or not HTTP_HEADER_NAME.fullmatch(header):
        raise RuntimeError("FANBACKSTAGE_TRUSTED_COUNTRY_HEADER is invalid for production")
    if len(cidrs) > 32:
        raise RuntimeError("FANBACKSTAGE_TRUSTED_PROXY_CIDRS is invalid for production")
    seen: set[str] = set()
    for value in cidrs:
        try:
            network = ip_network(value, strict=True)
        except ValueError as exc:
            raise RuntimeError(
                "FANBACKSTAGE_TRUSTED_PROXY_CIDRS is invalid for production"
            ) from exc
        canonical = str(network)
        if (
            canonical in seen
            or network.prefixlen == 0
            or network.is_unspecified
            or network.is_multicast
            or network.is_loopback
            or network.is_link_local
            or (network.version == 4 and network.prefixlen < 16)
            or (network.version == 6 and network.prefixlen < 48)
        ):
            raise RuntimeError("FANBACKSTAGE_TRUSTED_PROXY_CIDRS is unsafe for production")
        seen.add(canonical)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="../../.env", env_prefix="FANBACKSTAGE_", extra="ignore"
    )
    environment: str = "development"
    app_name: str = "FanBackstage"
    web_origin: str = "http://localhost:3000"
    api_origin: str = "http://localhost:8000"
    database_url: str = "postgresql+asyncpg://fanbackstage:fanbackstage@localhost:5432/fanbackstage"
    redis_url: str = "redis://localhost:6379/0"
    session_secret: str = "change-me-for-development-only"
    session_cookie_name: str = "fanbackstage_session"
    cookie_secure: bool = False
    session_ttl_hours: int = 24
    auth_rate_limit_attempts: int = 10
    auth_rate_limit_window_seconds: int = 60
    media_rate_limit_attempts: int = 30
    media_rate_limit_window_seconds: int = 60
    social_rate_limit_attempts: int = 60
    social_rate_limit_window_seconds: int = 60
    messaging_rate_limit_attempts: int = 30
    messaging_rate_limit_window_seconds: int = 60
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = False
    smtp_start_tls: bool = False
    email_from: str = "no-reply@fanbackstage.local"
    notification_webhook_secret: str = "development-notification-webhook-secret"
    notification_max_attempts: int = 3
    notification_retry_base_seconds: int = 30
    notification_unsubscribe_ttl_days: int = 30
    adult_attestation_version: str = "adult-self-attestation-v1"
    adult_access_cookie_name: str = "fanbackstage_adult_access"
    adult_access_cookie_ttl_hours: int = 24 * 30
    age_assurance_provider: str = "development_self_attestation"
    age_test_provider_enabled: bool = False
    age_provider_timeout_seconds: float = 10.0
    age_provider_probe_max_age_seconds: int = 86400
    manual_age_review_max_days: int = 90
    verifymyage_environment: str = "sandbox"
    verifymyage_client_id: str = ""
    verifymyage_client_secret: str = ""
    anonymous_compliance_cookie_name: str = "fanbackstage_compliance_session"
    anonymous_compliance_session_ttl_hours: int = 24
    trusted_country_header: str = ""
    trusted_proxy_cidrs: str = ""
    internal_country_handoff_secret: str = ""
    compliance_fallback_country: str = ""
    kyc_provider: str = "development"
    development_kyc_http_enabled: bool = False
    storage_endpoint_url: str = "http://localhost:9000"
    storage_public_endpoint_url: str | None = None
    storage_access_key: str = "fanbackstage"
    storage_secret_key: str = "fanbackstage-development-only"
    storage_bucket: str = "fanbackstage-private"
    storage_region: str = "us-east-1"
    media_url_ttl_seconds: int = 300
    media_max_image_bytes: int = 20 * 1024 * 1024
    media_max_video_bytes: int = 500 * 1024 * 1024
    media_max_gallery_items: int = 100
    media_max_video_duration_seconds: int = 3600
    media_processing_max_attempts: int = 3
    payment_provider: str = "development"
    payment_webhook_secret: str = "development-payment-webhook-secret"
    finance_default_commission_basis_points: int = 2000
    creator_earnings_settlement_seconds: int = 0
    subscription_grace_period_days: int = 3
    subscription_renewal_retry_limit: int = 3
    subscription_renewal_retry_seconds: int = 300
    livekit_url: str = "ws://localhost:7880"
    livekit_api_key: str = "devkey"
    livekit_api_secret: str = "fanbackstage-livekit-development-secret-2026"
    livekit_token_ttl_seconds: int = 300
    streaming_reconnect_grace_seconds: int = 30
    streaming_rate_limit_attempts: int = 30
    streaming_rate_limit_window_seconds: int = 60
    discovery_rate_limit_attempts: int = 60
    discovery_rate_limit_window_seconds: int = 60
    demo_seed_enabled: bool = False

    def validate_production(self) -> None:
        if self.environment not in {"development", "test", "staging", "production"}:
            raise RuntimeError("FANBACKSTAGE_ENVIRONMENT is invalid")
        if not self.adult_attestation_version.strip() or len(self.adult_attestation_version) > 64:
            raise RuntimeError("FANBACKSTAGE_ADULT_ATTESTATION_VERSION is invalid")
        if not self.adult_access_cookie_name.strip() or len(self.adult_access_cookie_name) > 128:
            raise RuntimeError("FANBACKSTAGE_ADULT_ACCESS_COOKIE_NAME is invalid")
        if not 1 <= self.adult_access_cookie_ttl_hours <= 24 * 365:
            raise RuntimeError("FANBACKSTAGE_ADULT_ACCESS_COOKIE_TTL_HOURS is invalid")
        if (
            not self.anonymous_compliance_cookie_name.strip()
            or len(self.anonymous_compliance_cookie_name) > 128
        ):
            raise RuntimeError("FANBACKSTAGE_ANONYMOUS_COMPLIANCE_COOKIE_NAME is invalid")
        if not 1 <= self.anonymous_compliance_session_ttl_hours <= 24 * 30:
            raise RuntimeError("FANBACKSTAGE_ANONYMOUS_COMPLIANCE_SESSION_TTL_HOURS is invalid")
        if not 0.5 <= self.age_provider_timeout_seconds <= 60:
            raise RuntimeError("FANBACKSTAGE_AGE_PROVIDER_TIMEOUT_SECONDS is invalid")
        if not 60 <= self.age_provider_probe_max_age_seconds <= 7 * 86400:
            raise RuntimeError("FANBACKSTAGE_AGE_PROVIDER_PROBE_MAX_AGE_SECONDS is invalid")
        if not 1 <= self.manual_age_review_max_days <= 365:
            raise RuntimeError("FANBACKSTAGE_MANUAL_AGE_REVIEW_MAX_DAYS is invalid")
        if self.verifymyage_environment not in {"sandbox", "production"}:
            raise RuntimeError("FANBACKSTAGE_VERIFYMYAGE_ENVIRONMENT is invalid")
        if self.compliance_fallback_country:
            from app.compliance.types import ISO_ALPHA2_CODES

            if self.compliance_fallback_country.strip().upper() not in ISO_ALPHA2_CODES:
                raise RuntimeError("FANBACKSTAGE_COMPLIANCE_FALLBACK_COUNTRY is invalid")
        if self.age_test_provider_enabled and self.environment not in {"development", "test"}:
            raise RuntimeError(
                "FANBACKSTAGE_AGE_TEST_PROVIDER_ENABLED is limited to development and test"
            )
        if self.development_kyc_http_enabled and self.environment not in {
            "development",
            "test",
        }:
            raise RuntimeError(
                "FANBACKSTAGE_DEVELOPMENT_KYC_HTTP_ENABLED is limited to development and test"
            )
        if not 0 <= self.finance_default_commission_basis_points <= 10000:
            raise RuntimeError("FANBACKSTAGE_FINANCE_DEFAULT_COMMISSION_BASIS_POINTS is invalid")
        if self.creator_earnings_settlement_seconds < 0:
            raise RuntimeError("FANBACKSTAGE_CREATOR_EARNINGS_SETTLEMENT_SECONDS is invalid")
        if (
            self.subscription_grace_period_days < 0
            or self.subscription_renewal_retry_limit < 0
            or self.subscription_renewal_retry_seconds <= 0
        ):
            raise RuntimeError("Subscription grace/retry configuration is invalid")
        if not 1 <= self.livekit_token_ttl_seconds <= 300:
            raise RuntimeError("FANBACKSTAGE_LIVEKIT_TOKEN_TTL_SECONDS is invalid")
        if not 1 <= self.media_url_ttl_seconds <= 300:
            raise RuntimeError("FANBACKSTAGE_MEDIA_URL_TTL_SECONDS is invalid")
        if not 1 <= self.smtp_port <= 65535:
            raise RuntimeError("FANBACKSTAGE_SMTP_PORT is invalid")
        if self.smtp_use_tls and self.smtp_start_tls:
            raise RuntimeError(
                "FANBACKSTAGE_SMTP_USE_TLS and FANBACKSTAGE_SMTP_START_TLS cannot both be true"
            )
        if self.environment != "production":
            if self.age_assurance_provider not in IMPLEMENTED_AGE_ASSURANCE_PROVIDERS:
                raise RuntimeError("FANBACKSTAGE_AGE_ASSURANCE_PROVIDER is not implemented")
            if self.kyc_provider not in IMPLEMENTED_KYC_PROVIDERS:
                raise RuntimeError("FANBACKSTAGE_KYC_PROVIDER is not implemented")
            if self.payment_provider not in IMPLEMENTED_PAYMENT_PROVIDERS:
                raise RuntimeError("FANBACKSTAGE_PAYMENT_PROVIDER is not implemented")
            return
        _require_production_value(
            "FANBACKSTAGE_SESSION_SECRET",
            self.session_secret,
            minimum_length=32,
            forbidden={"change-me-for-development-only"},
        )
        _require_production_value(
            "FANBACKSTAGE_NOTIFICATION_WEBHOOK_SECRET",
            self.notification_webhook_secret,
            minimum_length=32,
            forbidden={"development-notification-webhook-secret"},
        )
        _require_production_value(
            "FANBACKSTAGE_INTERNAL_COUNTRY_HANDOFF_SECRET",
            self.internal_country_handoff_secret,
            minimum_length=32,
        )
        _require_production_value(
            "FANBACKSTAGE_PAYMENT_WEBHOOK_SECRET",
            self.payment_webhook_secret,
            minimum_length=32,
            forbidden={"development-payment-webhook-secret"},
        )
        _require_production_value(
            "FANBACKSTAGE_LIVEKIT_API_KEY",
            self.livekit_api_key,
            minimum_length=8,
            forbidden={"devkey"},
        )
        _require_production_value(
            "FANBACKSTAGE_LIVEKIT_API_SECRET",
            self.livekit_api_secret,
            minimum_length=24,
            forbidden={"fanbackstage-livekit-development-secret-2026"},
        )
        if not self.cookie_secure:
            raise RuntimeError("Production requires secure session cookies")
        _require_production_endpoint(
            "FANBACKSTAGE_WEB_ORIGIN", self.web_origin, scheme="https", origin=True
        )
        _require_production_endpoint(
            "FANBACKSTAGE_API_ORIGIN", self.api_origin, scheme="https", origin=True
        )
        _validate_production_country_proxy(
            self.trusted_country_header,
            self.trusted_proxy_cidrs,
        )
        _require_production_endpoint("FANBACKSTAGE_LIVEKIT_URL", self.livekit_url, scheme="wss")
        try:
            database = make_url(self.database_url)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("FANBACKSTAGE_DATABASE_URL is invalid") from exc
        if (
            database.drivername != "postgresql+asyncpg"
            or (database.host or "").lower() in {"", "localhost", "127.0.0.1", "::1"}
            or (database.username or "").lower() in {"", "fanbackstage", "postgres"}
            or (database.password or "").lower()
            in UNSAFE_PRODUCTION_VALUES | {"fanbackstage", "postgres"}
            or len(database.password or "") < 16
        ):
            raise RuntimeError("FANBACKSTAGE_DATABASE_URL uses unsafe production settings")
        database_query = database.normalized_query
        database_tls_modes = database_query.get("ssl", ())
        if (
            "sslmode" in database_query
            or len(database_tls_modes) != 1
            or database_tls_modes[0] not in PRODUCTION_POSTGRES_TLS_MODES
        ):
            raise RuntimeError(
                "FANBACKSTAGE_DATABASE_URL must configure SQLAlchemy/asyncpg TLS with "
                "exactly one ssl=require, ssl=verify-ca, or ssl=verify-full query value"
            )
        try:
            redis = urlsplit(self.redis_url)
            redis_port = redis.port
        except ValueError as exc:
            raise RuntimeError("FANBACKSTAGE_REDIS_URL is invalid") from exc
        if (
            redis.scheme != "rediss"
            or (redis.hostname or "").lower() in {"", "localhost", "127.0.0.1", "::1"}
            or redis_port is None
            or len(redis.password or "") < 16
            or (redis.password or "").lower() in UNSAFE_PRODUCTION_VALUES
        ):
            raise RuntimeError("FANBACKSTAGE_REDIS_URL uses unsafe production settings")
        if self.demo_seed_enabled:
            raise RuntimeError("FANBACKSTAGE_DEMO_SEED_ENABLED cannot be enabled in production")
        smtp_host = self.smtp_host.strip()
        if (
            smtp_host != self.smtp_host
            or not smtp_host
            or smtp_host.rstrip(".").lower() == "mailpit"
            or _is_local_host(smtp_host)
            or any(character.isspace() for character in smtp_host)
            or any(character in smtp_host for character in "/?#@")
        ):
            raise RuntimeError("FANBACKSTAGE_SMTP_HOST is invalid for production")
        if not (self.smtp_use_tls or self.smtp_start_tls):
            raise RuntimeError("Production SMTP requires implicit TLS or forced STARTTLS")
        _require_production_value(
            "FANBACKSTAGE_SMTP_USERNAME",
            self.smtp_username,
            minimum_length=1,
        )
        _require_production_value(
            "FANBACKSTAGE_SMTP_PASSWORD",
            self.smtp_password,
            minimum_length=16,
        )
        _require_production_endpoint(
            "FANBACKSTAGE_STORAGE_ENDPOINT_URL",
            self.storage_endpoint_url,
            scheme="https",
        )
        if self.storage_public_endpoint_url:
            _require_production_endpoint(
                "FANBACKSTAGE_STORAGE_PUBLIC_ENDPOINT_URL",
                self.storage_public_endpoint_url,
                scheme="https",
            )
        _require_production_value(
            "FANBACKSTAGE_STORAGE_ACCESS_KEY",
            self.storage_access_key,
            minimum_length=8,
            forbidden={"fanbackstage"},
        )
        _require_production_value(
            "FANBACKSTAGE_STORAGE_SECRET_KEY",
            self.storage_secret_key,
            minimum_length=32,
            forbidden={"fanbackstage-development-only"},
        )
        if self.age_assurance_provider not in IMPLEMENTED_AGE_ASSURANCE_PROVIDERS:
            raise RuntimeError("FANBACKSTAGE_AGE_ASSURANCE_PROVIDER is not implemented")
        if self.kyc_provider not in IMPLEMENTED_KYC_PROVIDERS:
            raise RuntimeError("FANBACKSTAGE_KYC_PROVIDER is not implemented")
        if self.payment_provider not in IMPLEMENTED_PAYMENT_PROVIDERS:
            raise RuntimeError("FANBACKSTAGE_PAYMENT_PROVIDER is not implemented")
        if self.age_assurance_provider == "development_self_attestation":
            raise RuntimeError("Development self-attested age assurance cannot run in production")
        if self.age_assurance_provider == "test" or self.age_test_provider_enabled:
            raise RuntimeError("The test age-assurance adapter cannot run in production")
        if self.age_assurance_provider != "verifymyage":
            raise RuntimeError("Production requires the VerifyMyAge age-assurance adapter")
        if self.verifymyage_environment != "production":
            raise RuntimeError(
                "Production requires FANBACKSTAGE_VERIFYMYAGE_ENVIRONMENT=production"
            )
        _require_production_value(
            "FANBACKSTAGE_VERIFYMYAGE_CLIENT_ID",
            self.verifymyage_client_id,
            minimum_length=8,
        )
        _require_production_value(
            "FANBACKSTAGE_VERIFYMYAGE_CLIENT_SECRET",
            self.verifymyage_client_secret,
            minimum_length=16,
        )
        if not self.compliance_fallback_country.strip():
            raise RuntimeError(
                "Production requires an explicit reviewed compliance fallback country"
            )
        if self.kyc_provider == "development":
            raise RuntimeError("The development KYC provider cannot run in production")
        if self.payment_provider == "development":
            raise RuntimeError("The development payment provider cannot run in production")

    def effective_compliance_fallback_country(self) -> str | None:
        if self.compliance_fallback_country.strip():
            return self.compliance_fallback_country.strip().upper()
        if self.environment in {"development", "test"}:
            return "PT"
        return None


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_production()
    return settings
