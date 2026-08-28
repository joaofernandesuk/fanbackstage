from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.compliance import AgeAssuranceLevel, ComplianceFeature

# Canonical ISO 3166-1 alpha-2 codes. This registry asserts identifiers only; it
# contains no legal or policy claims about any jurisdiction.
ISO_ALPHA2_CODES = frozenset(
    [
        "AD",
        "AE",
        "AF",
        "AG",
        "AI",
        "AL",
        "AM",
        "AO",
        "AQ",
        "AR",
        "AS",
        "AT",
        "AU",
        "AW",
        "AX",
        "AZ",
        "BA",
        "BB",
        "BD",
        "BE",
        "BF",
        "BG",
        "BH",
        "BI",
        "BJ",
        "BL",
        "BM",
        "BN",
        "BO",
        "BQ",
        "BR",
        "BS",
        "BT",
        "BV",
        "BW",
        "BY",
        "BZ",
        "CA",
        "CC",
        "CD",
        "CF",
        "CG",
        "CH",
        "CI",
        "CK",
        "CL",
        "CM",
        "CN",
        "CO",
        "CR",
        "CU",
        "CV",
        "CW",
        "CX",
        "CY",
        "CZ",
        "DE",
        "DJ",
        "DK",
        "DM",
        "DO",
        "DZ",
        "EC",
        "EE",
        "EG",
        "EH",
        "ER",
        "ES",
        "ET",
        "FI",
        "FJ",
        "FK",
        "FM",
        "FO",
        "FR",
        "GA",
        "GB",
        "GD",
        "GE",
        "GF",
        "GG",
        "GH",
        "GI",
        "GL",
        "GM",
        "GN",
        "GP",
        "GQ",
        "GR",
        "GS",
        "GT",
        "GU",
        "GW",
        "GY",
        "HK",
        "HM",
        "HN",
        "HR",
        "HT",
        "HU",
        "ID",
        "IE",
        "IL",
        "IM",
        "IN",
        "IO",
        "IQ",
        "IR",
        "IS",
        "IT",
        "JE",
        "JM",
        "JO",
        "JP",
        "KE",
        "KG",
        "KH",
        "KI",
        "KM",
        "KN",
        "KP",
        "KR",
        "KW",
        "KY",
        "KZ",
        "LA",
        "LB",
        "LC",
        "LI",
        "LK",
        "LR",
        "LS",
        "LT",
        "LU",
        "LV",
        "LY",
        "MA",
        "MC",
        "MD",
        "ME",
        "MF",
        "MG",
        "MH",
        "MK",
        "ML",
        "MM",
        "MN",
        "MO",
        "MP",
        "MQ",
        "MR",
        "MS",
        "MT",
        "MU",
        "MV",
        "MW",
        "MX",
        "MY",
        "MZ",
        "NA",
        "NC",
        "NE",
        "NF",
        "NG",
        "NI",
        "NL",
        "NO",
        "NP",
        "NR",
        "NU",
        "NZ",
        "OM",
        "PA",
        "PE",
        "PF",
        "PG",
        "PH",
        "PK",
        "PL",
        "PM",
        "PN",
        "PR",
        "PS",
        "PT",
        "PW",
        "PY",
        "QA",
        "RE",
        "RO",
        "RS",
        "RU",
        "RW",
        "SA",
        "SB",
        "SC",
        "SD",
        "SE",
        "SG",
        "SH",
        "SI",
        "SJ",
        "SK",
        "SL",
        "SM",
        "SN",
        "SO",
        "SR",
        "SS",
        "ST",
        "SV",
        "SX",
        "SY",
        "SZ",
        "TC",
        "TD",
        "TF",
        "TG",
        "TH",
        "TJ",
        "TK",
        "TL",
        "TM",
        "TN",
        "TO",
        "TR",
        "TT",
        "TV",
        "TW",
        "TZ",
        "UA",
        "UG",
        "UM",
        "US",
        "UY",
        "UZ",
        "VA",
        "VC",
        "VE",
        "VG",
        "VI",
        "VN",
        "VU",
        "WF",
        "WS",
        "YE",
        "YT",
        "ZA",
        "ZM",
        "ZW",
    ]
)

ASSURANCE_STRENGTH = {
    AgeAssuranceLevel.none: 0,
    AgeAssuranceLevel.self_attested: 1,
    AgeAssuranceLevel.low: 2,
    AgeAssuranceLevel.medium: 3,
    AgeAssuranceLevel.high: 4,
}


def normalize_country_code(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().upper()
    if normalized not in ISO_ALPHA2_CODES:
        raise ValueError("Country must be an ISO 3166-1 alpha-2 code")
    return normalized


class PolicyRules(BaseModel):
    """Complete, validated policy-template rules.

    Values are operational configuration, not statements of law. Production
    publication requires a separate reviewed policy revision.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool
    registration_allowed: bool
    creator_registration_allowed: bool
    purchases_allowed: bool
    subscriptions_allowed: bool
    ppv_allowed: bool
    live_allowed: bool
    marketplace_allowed: bool
    featuring_allowed: bool
    marketing_email_allowed: bool
    messaging_allowed: bool
    minimum_age: int = Field(ge=1, le=120)
    fan_age_verification_required: bool
    anonymous_adult_preview_allowed: bool
    required_assurance_level: AgeAssuranceLevel
    reverify_after_days: int | None = Field(default=None, ge=1, le=3650)
    grace_period_days: int = Field(default=0, ge=0, le=365)
    creator_identity_required: bool
    creator_age_verification_required: bool
    payout_kyc_required: bool
    co_performer_verification_required: bool
    release_required: bool
    explicit_public_preview_allowed: bool
    restricted_media_policy: str = Field(min_length=1, max_length=64)
    age_provider: str = Field(min_length=1, max_length=64)
    provider_policy_key: str | None = Field(default=None, max_length=128)


class PolicyOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool | None = None
    registration_allowed: bool | None = None
    creator_registration_allowed: bool | None = None
    purchases_allowed: bool | None = None
    subscriptions_allowed: bool | None = None
    ppv_allowed: bool | None = None
    live_allowed: bool | None = None
    marketplace_allowed: bool | None = None
    featuring_allowed: bool | None = None
    marketing_email_allowed: bool | None = None
    messaging_allowed: bool | None = None
    minimum_age: int | None = Field(default=None, ge=1, le=120)
    fan_age_verification_required: bool | None = None
    anonymous_adult_preview_allowed: bool | None = None
    required_assurance_level: AgeAssuranceLevel | None = None
    reverify_after_days: int | None = Field(default=None, ge=1, le=3650)
    grace_period_days: int | None = Field(default=None, ge=0, le=365)
    creator_identity_required: bool | None = None
    creator_age_verification_required: bool | None = None
    payout_kyc_required: bool | None = None
    co_performer_verification_required: bool | None = None
    release_required: bool | None = None
    explicit_public_preview_allowed: bool | None = None
    restricted_media_policy: str | None = Field(default=None, min_length=1, max_length=64)
    age_provider: str | None = Field(default=None, min_length=1, max_length=64)
    provider_policy_key: str | None = Field(default=None, max_length=128)


@dataclass(frozen=True)
class JurisdictionSignals:
    verification_country: str | None = None
    kyc_country: str | None = None
    billing_country: str | None = None
    trusted_proxy_country: str | None = None
    request_country: str | None = None
    account_country: str | None = None
    selected_country: str | None = None

    def normalized(self) -> JurisdictionSignals:
        return JurisdictionSignals(
            verification_country=normalize_country_code(self.verification_country),
            kyc_country=normalize_country_code(self.kyc_country),
            billing_country=normalize_country_code(self.billing_country),
            trusted_proxy_country=normalize_country_code(self.trusted_proxy_country),
            request_country=normalize_country_code(self.request_country),
            account_country=normalize_country_code(self.account_country),
            selected_country=normalize_country_code(self.selected_country),
        )

    def countries(self) -> tuple[str, ...]:
        normalized = self.normalized()
        values = (
            normalized.verification_country,
            normalized.kyc_country,
            normalized.billing_country,
            normalized.trusted_proxy_country,
            normalized.request_country,
            normalized.account_country,
        )
        return tuple(dict.fromkeys(value for value in values if value))

    def primary(self) -> str | None:
        values = self.countries()
        return values[0] if values else None


def resolve_jurisdiction_candidates(
    signals: JurisdictionSignals,
    *,
    fallback_country: str | None,
    allow_untrusted_selection: bool,
) -> tuple[str, ...]:
    """Return conflict-preserving current-country candidates.

    `selected_country` is a user choice, never an authority. It can narrow or
    agree with trusted evidence, but disagreement remains visible. Development
    and tests may explicitly use it without an authority so local/E2E flows do
    not pretend an implicit fallback is GeoIP evidence.
    """

    normalized = signals.normalized()
    authoritative = normalized.countries()
    selected = normalized.selected_country
    fallback = normalize_country_code(fallback_country)
    if authoritative:
        if selected and selected not in authoritative:
            return (*authoritative, selected)
        return authoritative
    if selected and allow_untrusted_selection:
        return (selected,)
    if selected and fallback:
        return (fallback,) if selected == fallback else (fallback, selected)
    if selected:
        return ()
    return (fallback,) if fallback else ()


@dataclass(frozen=True)
class ComplianceDecision:
    allowed: bool
    code: str
    action: str | None
    reason: str
    feature: ComplianceFeature
    jurisdiction: str | None
    policy_id: UUID | None
    policy_version: int | None
    required_minimum_age: int | None
    required_assurance_level: AgeAssuranceLevel
    achieved_assurance_level: AgeAssuranceLevel
    age_access_allowed: bool
    feature_allowed: bool
    country_conflict: bool
    verification_expires_at: datetime | None

    def public_dict(self) -> dict[str, object]:
        return {
            "allowed": self.allowed,
            "code": self.code,
            "action": self.action,
            "reason": self.reason,
            "feature": self.feature.value,
            "jurisdiction": self.jurisdiction,
            "policy_version": self.policy_version,
            "required_minimum_age": self.required_minimum_age,
            "required_assurance_level": self.required_assurance_level.value,
            "achieved_assurance_level": self.achieved_assurance_level.value,
            "age_access_allowed": self.age_access_allowed,
            "feature_allowed": self.feature_allowed,
            "country_conflict": self.country_conflict,
            "verification_expires_at": (
                self.verification_expires_at.isoformat()
                if self.verification_expires_at is not None
                else None
            ),
        }


class ComplianceAccessError(PermissionError):
    def __init__(self, decision: ComplianceDecision) -> None:
        super().__init__(decision.reason)
        self.decision = decision
        self.code = decision.code
        self.action = decision.action
        self.status_code = 401 if decision.action == "LOGIN" else 403


def require_compliance_access(decision: ComplianceDecision) -> ComplianceDecision:
    """Return an allowed decision or raise the canonical structured denial."""

    if not decision.allowed:
        raise ComplianceAccessError(decision)
    return decision
