from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.models.compliance import AgeAssuranceLevel, AgeVerificationStatus


class ProviderError(RuntimeError):
    """Stable provider-boundary error with no upstream payload attached."""

    def __init__(self, message: str, *, code: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class ProviderConfigurationError(ProviderError):
    pass


class ProviderUnavailableError(ProviderError):
    pass


@dataclass(frozen=True)
class ProviderCapabilities:
    browser_callback: bool
    webhook_callback: bool
    revocation: bool
    assurance_levels: tuple[AgeAssuranceLevel, ...]

    def public_dict(self) -> dict[str, object]:
        return {
            "browser_callback": self.browser_callback,
            "webhook_callback": self.webhook_callback,
            "revocation": self.revocation,
            "assurance_levels": [level.value for level in self.assurance_levels],
        }


@dataclass(frozen=True)
class ProviderStartRequest:
    country_code: str
    state: str
    redirect_uri: str
    user_reference: str | None = None


@dataclass(frozen=True)
class ProviderStartResult:
    authorization_url: str


@dataclass(frozen=True)
class ProviderVerificationResult:
    provider_verification_id: str
    status: AgeVerificationStatus
    age_verified: bool
    achieved_assurance_level: AgeAssuranceLevel
    achieved_minimum_age: int | None
    verified_at: datetime | None = None
    expires_at: datetime | None = None
    failure_reason_code: str | None = None
    retryable: bool = False


@dataclass(frozen=True)
class ProviderDiagnostic:
    healthy: bool
    configuration_complete: bool
    error_code: str | None
    callback_url: str
    allowed_redirect: bool | None
    capabilities: ProviderCapabilities


class AgeVerificationProvider(Protocol):
    name: str
    environment: str

    def get_capabilities(self) -> ProviderCapabilities: ...

    async def create_verification_session(
        self, request: ProviderStartRequest
    ) -> ProviderStartResult: ...

    async def exchange_browser_callback(self, code: str) -> ProviderVerificationResult: ...

    async def get_provider_status(self, callback_url: str) -> ProviderDiagnostic: ...
