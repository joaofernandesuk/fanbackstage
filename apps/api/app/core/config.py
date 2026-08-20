from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file="../../.env", env_prefix="FANBACKSTAGE_", extra="ignore"
    )
    environment: str = "development"
    app_name: str = "FanBackstage"
    web_origin: str = "http://localhost:3000"
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
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    email_from: str = "no-reply@fanbackstage.local"
    kyc_provider: str = "development"
    storage_endpoint_url: str = "http://localhost:9000"
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

    def validate_production(self) -> None:
        if (
            self.environment == "production"
            and self.session_secret == "change-me-for-development-only"
        ):
            raise RuntimeError("FANBACKSTAGE_SESSION_SECRET must be set in production")
        if self.environment == "production" and self.kyc_provider == "development":
            raise RuntimeError("The development KYC provider cannot run in production")
        if self.environment == "production" and self.payment_provider == "development":
            raise RuntimeError("The development payment provider cannot run in production")
        if not 0 <= self.finance_default_commission_basis_points <= 10000:
            raise RuntimeError("FANBACKSTAGE_FINANCE_DEFAULT_COMMISSION_BASIS_POINTS is invalid")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_production()
    return settings
