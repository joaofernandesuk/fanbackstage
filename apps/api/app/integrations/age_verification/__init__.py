"""Age-assurance provider adapters.

Only normalized provider outcomes cross this package boundary. OAuth tokens,
documents, and provider response payloads must never be persisted.
"""

from app.integrations.age_verification.base import (
    AgeVerificationProvider,
    ProviderCapabilities,
    ProviderConfigurationError,
    ProviderDiagnostic,
    ProviderError,
    ProviderStartRequest,
    ProviderStartResult,
    ProviderUnavailableError,
    ProviderVerificationResult,
)
from app.integrations.age_verification.registry import get_age_verification_provider

__all__ = [
    "AgeVerificationProvider",
    "ProviderCapabilities",
    "ProviderConfigurationError",
    "ProviderDiagnostic",
    "ProviderError",
    "ProviderStartRequest",
    "ProviderStartResult",
    "ProviderUnavailableError",
    "ProviderVerificationResult",
    "get_age_verification_provider",
]
