from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode
from uuid import uuid4

from app.integrations.age_verification.base import (
    ProviderCapabilities,
    ProviderDiagnostic,
    ProviderStartRequest,
    ProviderStartResult,
    ProviderUnavailableError,
    ProviderVerificationResult,
)
from app.models.compliance import AgeAssuranceLevel, AgeVerificationStatus


class TestAgeVerificationProvider:
    """Deterministic adapter for automated tests; registry blocks it elsewhere."""

    __test__ = False
    name = "test"
    environment = "test"

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            browser_callback=True,
            webhook_callback=False,
            revocation=False,
            assurance_levels=(AgeAssuranceLevel.low, AgeAssuranceLevel.medium),
        )

    async def create_verification_session(
        self, request: ProviderStartRequest
    ) -> ProviderStartResult:
        return ProviderStartResult(
            authorization_url=f"{request.redirect_uri}?{urlencode({'state': request.state, 'code': 'approved'})}"
        )

    async def exchange_browser_callback(self, code: str) -> ProviderVerificationResult:
        now = datetime.now(UTC)
        if code == "unavailable":
            raise ProviderUnavailableError(
                "Test provider is unavailable",
                code="PROVIDER_UNAVAILABLE",
                retryable=True,
            )
        if code == "approved":
            return ProviderVerificationResult(
                provider_verification_id=f"test-{uuid4().hex}",
                status=AgeVerificationStatus.verified,
                age_verified=True,
                achieved_assurance_level=AgeAssuranceLevel.medium,
                achieved_minimum_age=18,
                verified_at=now,
                expires_at=now + timedelta(days=30),
            )
        if code == "review":
            return ProviderVerificationResult(
                provider_verification_id=f"test-{uuid4().hex}",
                status=AgeVerificationStatus.review_required,
                age_verified=False,
                achieved_assurance_level=AgeAssuranceLevel.none,
                achieved_minimum_age=None,
                failure_reason_code="MANUAL_REVIEW_REQUIRED",
            )
        if code == "expired":
            return ProviderVerificationResult(
                provider_verification_id=f"test-{uuid4().hex}",
                status=AgeVerificationStatus.expired,
                age_verified=True,
                achieved_assurance_level=AgeAssuranceLevel.medium,
                achieved_minimum_age=18,
                failure_reason_code="PROVIDER_RESULT_EXPIRED",
            )
        return ProviderVerificationResult(
            provider_verification_id=f"test-{uuid4().hex}",
            status=AgeVerificationStatus.failed,
            age_verified=False,
            achieved_assurance_level=AgeAssuranceLevel.none,
            achieved_minimum_age=None,
            failure_reason_code="AGE_NOT_VERIFIED",
        )

    async def get_provider_status(self, callback_url: str) -> ProviderDiagnostic:
        return ProviderDiagnostic(
            healthy=True,
            configuration_complete=True,
            error_code=None,
            callback_url=callback_url,
            allowed_redirect=True,
            capabilities=self.get_capabilities(),
        )
