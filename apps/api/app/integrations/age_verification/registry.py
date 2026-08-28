from __future__ import annotations

from app.core.config import Settings, get_settings
from app.integrations.age_verification.base import (
    AgeVerificationProvider,
    ProviderConfigurationError,
)
from app.integrations.age_verification.test_provider import TestAgeVerificationProvider
from app.integrations.age_verification.verifymyage import VerifyMyAgeProvider


def get_age_verification_provider(
    name: str | None = None, *, settings: Settings | None = None
) -> AgeVerificationProvider:
    settings = settings or get_settings()
    provider_name = name or settings.age_assurance_provider
    if provider_name == "verifymyage":
        return VerifyMyAgeProvider(
            environment=settings.verifymyage_environment,
            client_id=settings.verifymyage_client_id,
            client_secret=settings.verifymyage_client_secret,
            timeout_seconds=settings.age_provider_timeout_seconds,
        )
    if provider_name == "test":
        if settings.environment not in {"development", "test"}:
            raise ProviderConfigurationError(
                "The test age-assurance adapter is unavailable in this environment",
                code="TEST_PROVIDER_BLOCKED",
            )
        if settings.environment == "development" and not settings.age_test_provider_enabled:
            raise ProviderConfigurationError(
                "The test age-assurance adapter is disabled",
                code="TEST_PROVIDER_DISABLED",
            )
        return TestAgeVerificationProvider()
    raise ProviderConfigurationError(
        "Configured age-assurance provider has no durable adapter",
        code="PROVIDER_NOT_IMPLEMENTED",
    )
