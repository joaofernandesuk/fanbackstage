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

    def validate_production(self) -> None:
        if (
            self.environment == "production"
            and self.session_secret == "change-me-for-development-only"
        ):
            raise RuntimeError("FANBACKSTAGE_SESSION_SECRET must be set in production")
        if self.environment == "production" and self.kyc_provider == "development":
            raise RuntimeError("The development KYC provider cannot run in production")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_production()
    return settings
