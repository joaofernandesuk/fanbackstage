from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from urllib.parse import urlencode

import httpx

from app.core.logging import configure_sensitive_http_logging
from app.integrations.age_verification.base import (
    ProviderCapabilities,
    ProviderConfigurationError,
    ProviderDiagnostic,
    ProviderError,
    ProviderStartRequest,
    ProviderStartResult,
    ProviderUnavailableError,
    ProviderVerificationResult,
)
from app.models.compliance import AgeAssuranceLevel, AgeVerificationStatus

VERIFYMYAGE_BASE_URLS = {
    "production": "https://oauth.verifymyage.com",
    "sandbox": "https://sandbox.verifymyage.com",
}


class VerifyMyAgeProvider:
    name = "verifymyage"

    def __init__(
        self,
        *,
        environment: str,
        client_id: str,
        client_secret: str,
        timeout_seconds: float = 10.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        configure_sensitive_http_logging()
        if environment not in VERIFYMYAGE_BASE_URLS:
            raise ProviderConfigurationError(
                "VerifyMyAge environment is invalid", code="INVALID_ENVIRONMENT"
            )
        self.environment = environment
        self.client_id = client_id.strip()
        self.client_secret = client_secret.strip()
        self.base_url = VERIFYMYAGE_BASE_URLS[environment]
        self._timeout_seconds = timeout_seconds
        self._client = client

    def _require_oauth_configuration(self) -> None:
        if not self.client_id or not self.client_secret:
            raise ProviderConfigurationError(
                "VerifyMyAge OAuth credentials are incomplete",
                code="OAUTH_CONFIGURATION_INCOMPLETE",
            )

    def get_capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            browser_callback=True,
            webhook_callback=False,
            revocation=False,
            assurance_levels=(AgeAssuranceLevel.low,),
        )

    async def create_verification_session(
        self, request: ProviderStartRequest
    ) -> ProviderStartResult:
        self._require_oauth_configuration()
        params = {
            "client_id": self.client_id,
            "scope": "adult",
            "country": request.country_code.lower(),
            "redirect_uri": request.redirect_uri,
            "state": request.state,
        }
        if request.user_reference:
            params["user_id"] = request.user_reference
        return ProviderStartResult(
            authorization_url=f"{self.base_url}/oauth/authorize?{urlencode(params)}"
        )

    async def exchange_browser_callback(self, code: str) -> ProviderVerificationResult:
        self._require_oauth_configuration()
        if not code.strip():
            raise ProviderError("Provider callback code is missing", code="CODE_MISSING")
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout_seconds)
        try:
            token_response = await client.post(
                f"{self.base_url}/oauth/token",
                json={"code": code},
                auth=(self.client_id, self.client_secret),
            )
            token_response.raise_for_status()
            token_payload = token_response.json()
            if not isinstance(token_payload, dict):
                raise ProviderError(
                    "VerifyMyAge token response was incomplete",
                    code="TOKEN_RESPONSE_INCOMPLETE",
                )
            access_token = token_payload.get("access_token")
            if (
                not isinstance(access_token, str)
                or not access_token.strip()
                or len(access_token) > 8192
            ):
                raise ProviderError(
                    "VerifyMyAge token response was incomplete",
                    code="TOKEN_RESPONSE_INCOMPLETE",
                )
            result_response = await client.get(
                f"{self.base_url}/users/me", params={"access_token": access_token}
            )
            result_response.raise_for_status()
            payload = result_response.json()
            if not isinstance(payload, dict):
                raise ProviderError(
                    "VerifyMyAge result was incomplete", code="RESULT_RESPONSE_INCOMPLETE"
                )
        except ProviderError:
            raise
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            if status_code == 429 or status_code >= 500:
                raise ProviderUnavailableError(
                    "VerifyMyAge could not return a verification result",
                    code="PROVIDER_UNAVAILABLE",
                    retryable=True,
                ) from exc
            code = (
                "PROVIDER_CREDENTIALS_REJECTED"
                if status_code in {401, 403}
                else "PROVIDER_REQUEST_REJECTED"
            )
            raise ProviderError(
                "VerifyMyAge rejected the verification request",
                code=code,
                retryable=False,
            ) from exc
        except (httpx.RequestError, ValueError, TypeError) as exc:
            raise ProviderUnavailableError(
                "VerifyMyAge could not return a verification result",
                code="PROVIDER_UNAVAILABLE",
                retryable=True,
            ) from exc
        finally:
            if owns_client:
                await client.aclose()

        provider_id = payload.get("id")
        age_verified = payload.get("age_verified")
        threshold = payload.get("threshold")
        provider_id_supported = isinstance(provider_id, (str, int)) and not isinstance(
            provider_id, bool
        )
        normalized_provider_id = str(provider_id).strip() if provider_id_supported else ""
        if (
            not normalized_provider_id
            or len(normalized_provider_id) > 255
            or not isinstance(age_verified, bool)
        ):
            raise ProviderError(
                "VerifyMyAge result was incomplete", code="RESULT_RESPONSE_INCOMPLETE"
            )
        if threshold is None:
            achieved_minimum_age = None
        elif (
            isinstance(threshold, int) and not isinstance(threshold, bool) and 1 <= threshold <= 120
        ):
            achieved_minimum_age = threshold
        else:
            raise ProviderError("VerifyMyAge result was invalid", code="RESULT_RESPONSE_INVALID")
        if age_verified and achieved_minimum_age is None:
            raise ProviderError(
                "VerifyMyAge verified result omitted its age threshold",
                code="RESULT_RESPONSE_INVALID",
            )
        if age_verified:
            return ProviderVerificationResult(
                provider_verification_id=normalized_provider_id,
                status=AgeVerificationStatus.verified,
                age_verified=True,
                achieved_assurance_level=AgeAssuranceLevel.low,
                achieved_minimum_age=achieved_minimum_age,
                verified_at=datetime.now(UTC),
            )
        return ProviderVerificationResult(
            provider_verification_id=normalized_provider_id,
            status=AgeVerificationStatus.failed,
            age_verified=False,
            achieved_assurance_level=AgeAssuranceLevel.none,
            achieved_minimum_age=achieved_minimum_age,
            failure_reason_code="AGE_NOT_VERIFIED",
        )

    async def get_provider_status(self, callback_url: str) -> ProviderDiagnostic:
        capabilities = self.get_capabilities()
        if not self.client_id or not self.client_secret:
            return ProviderDiagnostic(
                healthy=False,
                configuration_complete=False,
                error_code="CONFIGURATION_INCOMPLETE",
                callback_url=callback_url,
                allowed_redirect=None,
                capabilities=capabilities,
            )
        request_uri = "/v1/business/allowed-redirects"
        signature = hmac.new(
            self.client_secret.encode(), request_uri.encode(), hashlib.sha256
        ).hexdigest()
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=self._timeout_seconds)
        try:
            response = await client.get(
                f"{self.base_url}{request_uri}",
                headers={"Authorization": f"hmac {self.client_id}:{signature}"},
            )
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict):
                redirects = payload.get("body", payload.get("allowed_redirects", payload))
            elif isinstance(payload, list):
                redirects = payload
            else:
                raise TypeError("VerifyMyAge diagnostic response is invalid")
            if isinstance(redirects, dict):
                redirects = redirects.get("redirects", [])
            allowed = isinstance(redirects, list) and callback_url in redirects
            return ProviderDiagnostic(
                healthy=True,
                configuration_complete=True,
                error_code=None if allowed else "CALLBACK_NOT_ALLOWED",
                callback_url=callback_url,
                allowed_redirect=allowed,
                capabilities=capabilities,
            )
        except (httpx.HTTPError, ValueError, TypeError):
            return ProviderDiagnostic(
                healthy=False,
                configuration_complete=True,
                error_code="DIAGNOSTIC_UNAVAILABLE",
                callback_url=callback_url,
                allowed_redirect=None,
                capabilities=capabilities,
            )
        finally:
            if owns_client:
                await client.aclose()
