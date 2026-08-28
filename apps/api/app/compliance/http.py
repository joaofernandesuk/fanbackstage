from __future__ import annotations

import ipaddress
import time
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from hmac import compare_digest
from hmac import new as hmac_new

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts import adult_access
from app.compliance.policy import current_verification_country, resolve_compliance_decision
from app.compliance.types import (
    ComplianceDecision,
    JurisdictionSignals,
    normalize_country_code,
    resolve_jurisdiction_candidates,
)
from app.core.config import get_settings
from app.legal import service as legal_service
from app.models.compliance import ComplianceFeature
from app.models.identity import User

INTERNAL_COUNTRY_HEADER = "X-FanBackstage-Internal-Country"
INTERNAL_COUNTRY_TIMESTAMP_HEADER = "X-FanBackstage-Internal-Country-Timestamp"
INTERNAL_COUNTRY_SIGNATURE_HEADER = "X-FanBackstage-Internal-Country-Signature"
INTERNAL_COUNTRY_MAX_AGE_SECONDS = 60


def request_country_from_internal_handoff(request: Request) -> str | None:
    """Verify the short-lived Next-to-API country handoff used for SSR legal copy."""

    settings = get_settings()
    secret = settings.internal_country_handoff_secret.strip()
    raw_country = request.headers.get(INTERNAL_COUNTRY_HEADER)
    raw_timestamp = request.headers.get(INTERNAL_COUNTRY_TIMESTAMP_HEADER)
    signature = request.headers.get(INTERNAL_COUNTRY_SIGNATURE_HEADER)
    if not secret or not raw_country or not raw_timestamp or not signature:
        return None
    try:
        country = normalize_country_code(raw_country)
        timestamp = int(raw_timestamp)
    except (TypeError, ValueError):
        return None
    if country is None or abs(int(time.time()) - timestamp) > INTERNAL_COUNTRY_MAX_AGE_SECONDS:
        return None
    payload = f"{country}\n{timestamp}\n{request.url.path}".encode()
    expected = hmac_new(secret.encode(), payload, sha256).hexdigest()
    if not compare_digest(expected, signature.strip().lower()):
        return None
    return country


def request_country_from_trusted_proxy(request: Request) -> str | None:
    """Read one configured GeoIP header only from a configured trusted peer."""

    settings = get_settings()
    header_name = settings.trusted_country_header.strip()
    cidr_values = [
        value.strip() for value in settings.trusted_proxy_cidrs.split(",") if value.strip()
    ]
    if not header_name or not cidr_values or request.client is None:
        return None
    try:
        peer = ipaddress.ip_address(request.client.host)
        networks = tuple(ipaddress.ip_network(value, strict=False) for value in cidr_values)
    except ValueError:
        return None
    if not any(peer in network for network in networks):
        return None
    value = request.headers.get(header_name)
    return value.strip().upper() if value else None


def jurisdiction_signals_from_request(
    request: Request,
    *,
    user: User | None,
    signals: JurisdictionSignals | None = None,
) -> JurisdictionSignals:
    supplied = signals or JurisdictionSignals()
    internal_country = request_country_from_internal_handoff(request)
    proxy_country = request_country_from_trusted_proxy(request)
    return JurisdictionSignals(
        verification_country=supplied.verification_country,
        kyc_country=supplied.kyc_country,
        billing_country=supplied.billing_country,
        trusted_proxy_country=internal_country or proxy_country,
        # Both are authoritative trust boundaries. Preserve disagreement as a
        # conflict rather than allowing either channel to shadow the other.
        request_country=(
            supplied.request_country
            or (
                proxy_country
                if internal_country and proxy_country and internal_country != proxy_country
                else None
            )
        ),
        account_country=supplied.account_country or (user.country_code if user else None),
        selected_country=supplied.selected_country,
    )


def resolve_request_jurisdiction(
    request: Request,
    *,
    user: User | None,
    signals: JurisdictionSignals | None = None,
) -> str | None:
    """Resolve supplied signals only; runtime routes use the async evidence-aware helper."""

    resolved_signals = jurisdiction_signals_from_request(request, user=user, signals=signals)
    try:
        settings = get_settings()
        countries = resolve_jurisdiction_candidates(
            resolved_signals,
            fallback_country=(
                settings.effective_compliance_fallback_country() if user is None else None
            ),
            allow_untrusted_selection=settings.environment in {"development", "test"},
        )
    except ValueError:
        countries = ()
    if len(countries) == 1:
        country = countries[0]
    else:
        country = None
    request.state.trusted_jurisdiction_code = country
    return country


async def resolve_request_jurisdiction_with_evidence(
    db: AsyncSession,
    request: Request,
    *,
    user: User | None,
    signals: JurisdictionSignals | None = None,
    now: datetime | None = None,
) -> str | None:
    """Resolve jurisdiction including current durable provider evidence."""

    current = now or datetime.now(UTC)
    settings = get_settings()
    merged = jurisdiction_signals_from_request(request, user=user, signals=signals)
    durable_country = await current_verification_country(
        db,
        user=user,
        anonymous_session_secret=request.cookies.get(settings.anonymous_compliance_cookie_name),
        now=current,
    )
    current_signals = JurisdictionSignals(
        verification_country=merged.verification_country,
        kyc_country=merged.kyc_country,
        billing_country=merged.billing_country,
        trusted_proxy_country=merged.trusted_proxy_country,
        request_country=merged.request_country,
        account_country=merged.account_country,
        selected_country=merged.selected_country,
    )
    try:
        countries = resolve_jurisdiction_candidates(
            current_signals,
            fallback_country=None,
            allow_untrusted_selection=settings.environment in {"development", "test"},
        )
        if not countries:
            merged = JurisdictionSignals(
                verification_country=durable_country,
                selected_country=merged.selected_country,
            )
            countries = resolve_jurisdiction_candidates(
                merged,
                fallback_country=(
                    settings.effective_compliance_fallback_country()
                    if user is None or durable_country is not None
                    else None
                ),
                allow_untrusted_selection=settings.environment in {"development", "test"},
            )
    except ValueError:
        countries = ()
    country = countries[0] if len(countries) == 1 else None
    request.state.trusted_jurisdiction_code = country
    request.state.compliance_jurisdiction = country
    request.state.compliance_signals = merged
    return country


async def resolve_request_compliance_decision(
    db: AsyncSession,
    request: Request,
    *,
    user: User | None,
    feature: ComplianceFeature,
    adult_restricted: bool = False,
    signals: JurisdictionSignals | None = None,
    now: datetime | None = None,
) -> ComplianceDecision:
    """Canonical request adapter for trusted country and assurance cookies."""

    settings = get_settings()
    merged = jurisdiction_signals_from_request(request, user=user, signals=signals)
    legacy = adult_access.resolve_adult_access(
        user,
        request.cookies.get(settings.adult_access_cookie_name),
        now=now,
    )
    decision = await resolve_compliance_decision(
        db,
        user=user,
        feature=feature,
        signals=merged,
        adult_restricted=adult_restricted,
        anonymous_session_secret=request.cookies.get(settings.anonymous_compliance_cookie_name),
        legacy_self_attested=legacy.allowed,
        legacy_self_attested_expires_at=legacy.expires_at,
        now=now,
    )
    decision, legal_version_ids = await compose_legal_acceptance_decision(
        db,
        user=user,
        decision=decision,
        now=now,
    )
    request.state.compliance_decision = decision
    request.state.compliance_jurisdiction = decision.jurisdiction
    request.state.trusted_jurisdiction_code = (
        None if decision.country_conflict else decision.jurisdiction
    )
    request.state.compliance_signals = merged
    request.state.legal_requirement_version_ids = legal_version_ids
    return decision


async def compose_legal_acceptance_decision(
    db: AsyncSession,
    *,
    user: User | None,
    decision: ComplianceDecision,
    now: datetime | None = None,
) -> tuple[ComplianceDecision, tuple[str, ...]]:
    """Compose mandatory legal acceptance into any canonical policy decision.

    Request handling, background enforcement, and the admin simulator must use
    this same request-independent step so operational previews cannot claim an
    account is allowed when runtime would deny it.
    """

    if (
        user is None
        or decision.jurisdiction is None
        or decision.country_conflict
        or not await legal_service.has_effective_acceptance_requirements(db, user, now=now)
    ):
        return decision, ()
    required = await legal_service.required_documents(
        db,
        user,
        jurisdiction_code=decision.jurisdiction,
        now=now,
    )
    if not required:
        return decision, ()
    version_ids = tuple(str(document.version_id) for document in required)
    return (
        replace(
            decision,
            allowed=False,
            code="LEGAL_ACCEPTANCE_REQUIRED",
            action="ACCEPT_LEGAL",
            reason="Current legal terms must be accepted before continuing.",
        ),
        version_ids,
    )
