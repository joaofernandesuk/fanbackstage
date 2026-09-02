import asyncio
import hashlib
import hmac
import importlib.util
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from starlette.requests import Request

from app.api.routes.compliance import age_verification_status
from app.compliance import policy as compliance_policy
from app.compliance.age_verification import (
    AgeVerificationError,
    attach_anonymous_session,
    complete_browser_callback,
    expire_due_verifications,
    notify_expiring_verifications,
    review_verification,
    revoke_verification,
    start_age_verification,
)
from app.compliance.http import (
    jurisdiction_signals_from_request,
    request_country_from_trusted_proxy,
    resolve_request_compliance_decision,
    resolve_request_jurisdiction,
    resolve_request_jurisdiction_with_evidence,
)
from app.compliance.policy import (
    CompliancePolicyError,
    create_feature_flag_revision,
    create_jurisdiction_revision,
    create_template_revision,
    current_verification_country,
    effective_policy_for_country,
    production_policy_readiness,
    resolve_compliance_decision,
    set_account_country,
    set_country_enabled,
)
from app.compliance.types import (
    JurisdictionSignals,
    PolicyOverrides,
    PolicyRules,
    resolve_jurisdiction_candidates,
)
from app.core.config import Settings
from app.creators.service import (
    development_verify,
    resolve_creator_compliance_eligibility,
    set_status,
)
from app.db.session import SessionLocal
from app.integrations.age_verification import ProviderConfigurationError, ProviderError
from app.integrations.age_verification.base import (
    ProviderStartRequest,
    ProviderStartResult,
    ProviderVerificationResult,
)
from app.integrations.age_verification.registry import get_age_verification_provider
from app.integrations.age_verification.verifymyage import VerifyMyAgeProvider
from app.models.audit import AuditEvent
from app.models.compliance import (
    AgeAssuranceLevel,
    AgeVerificationRecord,
    AgeVerificationStatus,
    AnonymousComplianceSession,
    ComplianceFeature,
    CompliancePolicyStatus,
    CompliancePolicyTemplate,
    CompliancePolicyTemplateRevision,
    CountryRegistry,
)
from app.models.creator import (
    CreatorProfile,
    CreatorStatus,
    CreatorVerification,
    VerificationStatus,
)
from app.models.identity import User
from app.models.notification import NotificationIntent


class _MigrationOperationsRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def get_bind(self) -> object:
        return object()

    def __getattr__(self, name: str) -> Callable[..., None]:
        def record(*args, **kwargs) -> None:
            self.calls.append((name, args, kwargs))

        return record


async def _actor_and_template(db):
    actor = await db.scalar(
        select(User).where(User.email == "compliance-policy-fixture@example.test")
    )
    template = await db.scalar(
        select(CompliancePolicyTemplate).where(CompliancePolicyTemplate.key == "test-baseline")
    )
    assert actor is not None and template is not None
    return actor, template


async def _publish_policy(
    db,
    *,
    country: str = "PT",
    rule_changes: dict | None = None,
    effective_from: datetime | None = None,
    is_demo: bool = True,
):
    now = effective_from or datetime.now(UTC)
    actor, template = await _actor_and_template(db)
    current = await effective_policy_for_country(db, "PT", now=now)
    assert current is not None
    rules = PolicyRules.model_validate({**current.rules.model_dump(), **(rule_changes or {})})
    template_revision = await create_template_revision(
        db,
        template_id=template.id,
        rules=rules,
        status=CompliancePolicyStatus.active,
        effective_from=now - timedelta(seconds=1),
        effective_until=None,
        actor_user_id=actor.id,
        reviewed_at=now,
        reviewed_by_user_id=actor.id,
        change_reason=f"Publish test successor for {country}",
        is_demo=is_demo,
    )
    jurisdiction = await create_jurisdiction_revision(
        db,
        country_code=country,
        template_revision_id=template_revision.id,
        overrides=PolicyOverrides(),
        status=CompliancePolicyStatus.active,
        effective_from=now - timedelta(seconds=1),
        effective_until=None,
        actor_user_id=actor.id,
        reviewed_at=now,
        reviewed_by_user_id=actor.id,
        change_reason=f"Publish test jurisdiction successor for {country}",
        is_demo=is_demo,
    )
    await db.flush()
    return actor, template_revision, jurisdiction, rules


async def _user(db, email: str, *, country_code: str | None = None) -> User:
    user = User(email=email, password_hash="not-authenticatable", country_code=country_code)
    db.add(user)
    await db.flush()
    return user


async def _age_record(
    db,
    *,
    user: User,
    status: AgeVerificationStatus = AgeVerificationStatus.verified,
    country_code: str = "PT",
    assurance: AgeAssuranceLevel = AgeAssuranceLevel.medium,
    threshold: int | None = 18,
    verified_at: datetime | None = None,
    expires_at: datetime | None = None,
    created_at: datetime | None = None,
) -> AgeVerificationRecord:
    policy = await effective_policy_for_country(db, "PT")
    assert policy is not None
    now = datetime.now(UTC)
    verified = verified_at or now
    row = AgeVerificationRecord(
        user_id=user.id,
        provider="test",
        provider_verification_id=f"test-{uuid4().hex}",
        state_hash=sha256(uuid4().hex.encode()).hexdigest(),
        safe_return_path="/account",
        country_code=country_code,
        applicable_policy_id=policy.jurisdiction_revision.id,
        applicable_policy_version=policy.jurisdiction_revision.version,
        required_minimum_age=18,
        achieved_minimum_age=threshold,
        required_assurance_level=AgeAssuranceLevel.medium,
        achieved_assurance_level=assurance,
        status=status,
        initiated_at=verified - timedelta(minutes=1),
        verified_at=verified if status is AgeVerificationStatus.verified else None,
        failed_at=now if status is AgeVerificationStatus.failed else None,
        expires_at=expires_at,
        revoked_at=now if status is AgeVerificationStatus.revoked else None,
        created_at=created_at or now,
    )
    db.add(row)
    await db.flush()
    return row


def _state_from_authorization_url(url: str) -> str:
    return parse_qs(urlsplit(url).query)["state"][0]


@pytest.mark.asyncio
async def test_append_only_successors_replace_effective_policy_and_feature(db_session):
    now = datetime.now(UTC)
    actor, _, first_jurisdiction, _ = await _publish_policy(
        db_session,
        rule_changes={"minimum_age": 19},
        effective_from=now,
    )
    _, _, second_jurisdiction, _ = await _publish_policy(
        db_session,
        rule_changes={"minimum_age": 21},
        effective_from=now + timedelta(microseconds=1),
    )
    effective = await effective_policy_for_country(db_session, "PT", now=now + timedelta(seconds=1))
    assert effective is not None
    assert effective.jurisdiction_revision.id == second_jurisdiction.id
    assert effective.jurisdiction_revision.version > first_jurisdiction.version
    assert effective.rules.minimum_age == 21

    await create_feature_flag_revision(
        db_session,
        feature=ComplianceFeature.messaging,
        country_scope="PT",
        enabled=False,
        effective_from=now - timedelta(seconds=1),
        effective_until=None,
        actor_user_id=actor.id,
        change_reason="Disable messaging in test",
    )
    second_feature = await create_feature_flag_revision(
        db_session,
        feature=ComplianceFeature.messaging,
        country_scope="PT",
        enabled=True,
        effective_from=now,
        effective_until=None,
        actor_user_id=actor.id,
        change_reason="Replace messaging flag in test",
    )
    feature_audit = await db_session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.event_type == "compliance.feature_flag_revision_created",
            AuditEvent.target_id == str(second_feature.id),
        )
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(1)
    )
    assert feature_audit is not None
    assert feature_audit.metadata_json["changes"] == [
        {"field": "enabled", "before": False, "after": True}
    ]
    assert feature_audit.metadata_json["effective_from"] == now.isoformat()
    assert (
        feature_audit.metadata_json["previous_effective_from"]
        == (now - timedelta(seconds=1)).isoformat()
    )
    decision = await resolve_compliance_decision(
        db_session,
        user=None,
        feature=ComplianceFeature.messaging,
        signals=JurisdictionSignals(request_country="PT"),
        now=now + timedelta(seconds=1),
    )
    assert decision.allowed


@pytest.mark.asyncio
async def test_platform_access_feature_flag_is_canonical_maintenance_control(db_session):
    now = datetime.now(UTC)
    actor, _ = await _actor_and_template(db_session)
    await create_feature_flag_revision(
        db_session,
        feature=ComplianceFeature.platform_access,
        country_scope="PT",
        enabled=False,
        effective_from=now - timedelta(seconds=1),
        effective_until=None,
        actor_user_id=actor.id,
        change_reason="Enter test maintenance",
    )
    denied = await resolve_compliance_decision(
        db_session,
        user=None,
        feature=ComplianceFeature.messaging,
        signals=JurisdictionSignals(request_country="PT"),
        now=now,
    )
    assert not denied.allowed
    assert denied.code == "FEATURE_UNAVAILABLE"
    assert denied.action == "RETRY_LATER"


@pytest.mark.asyncio
async def test_current_country_policy_reuses_sufficient_assurance_but_requires_stronger_result(
    db_session,
):
    now = datetime.now(UTC)
    await _publish_policy(
        db_session,
        country="US",
        rule_changes={
            "fan_age_verification_required": True,
            "required_assurance_level": AgeAssuranceLevel.medium,
        },
        effective_from=now,
    )
    await _publish_policy(
        db_session,
        country="GB",
        rule_changes={
            "fan_age_verification_required": True,
            "required_assurance_level": AgeAssuranceLevel.high,
        },
        effective_from=now,
    )
    user = await _user(db_session, "portable-assurance@example.test", country_code="PT")
    await _age_record(
        db_session,
        user=user,
        country_code="PT",
        assurance=AgeAssuranceLevel.medium,
        expires_at=now + timedelta(days=30),
    )
    user.country_code = "US"
    await db_session.flush()

    same_strength = await resolve_compliance_decision(
        db_session,
        user=user,
        feature=ComplianceFeature.purchases,
        now=now,
    )
    assert same_strength.allowed
    assert same_strength.jurisdiction == "US"
    stronger_user = await _user(db_session, "stronger-assurance@example.test", country_code="PT")
    await _age_record(
        db_session,
        user=stronger_user,
        country_code="PT",
        assurance=AgeAssuranceLevel.medium,
        expires_at=now + timedelta(days=30),
    )
    stronger_user.country_code = "GB"
    await db_session.flush()
    stronger = await resolve_compliance_decision(
        db_session,
        user=stronger_user,
        feature=ComplianceFeature.purchases,
        now=now,
    )
    assert not stronger.allowed
    assert stronger.jurisdiction == "GB"
    assert stronger.code == "AGE_ASSURANCE_INSUFFICIENT"


@pytest.mark.asyncio
async def test_request_resolver_reuses_or_rejects_country_a_evidence_under_country_b_policy(
    db_session,
):
    now = datetime.now(UTC)
    await _publish_policy(
        db_session,
        country="US",
        rule_changes={
            "fan_age_verification_required": True,
            "required_assurance_level": AgeAssuranceLevel.medium,
        },
        effective_from=now,
    )
    await _publish_policy(
        db_session,
        country="GB",
        rule_changes={
            "fan_age_verification_required": True,
            "required_assurance_level": AgeAssuranceLevel.high,
        },
        effective_from=now,
    )

    def request() -> Request:
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/compliance/decision",
                "headers": [],
                "query_string": b"",
                "client": ("203.0.113.5", 1234),
                "server": ("test", 80),
                "scheme": "http",
            }
        )

    sufficient = await _user(
        db_session, "request-country-sufficient@example.test", country_code="PT"
    )
    await _age_record(
        db_session,
        user=sufficient,
        country_code="PT",
        assurance=AgeAssuranceLevel.medium,
        expires_at=now + timedelta(days=30),
    )
    sufficient.country_code = "US"
    await db_session.flush()
    allowed = await resolve_request_compliance_decision(
        db_session,
        request(),
        user=sufficient,
        feature=ComplianceFeature.purchases,
        now=now,
    )
    assert allowed.allowed
    assert allowed.jurisdiction == "US"

    insufficient = await _user(
        db_session, "request-country-insufficient@example.test", country_code="PT"
    )
    await _age_record(
        db_session,
        user=insufficient,
        country_code="PT",
        assurance=AgeAssuranceLevel.medium,
        expires_at=now + timedelta(days=30),
    )
    insufficient.country_code = "GB"
    await db_session.flush()
    denied = await resolve_request_compliance_decision(
        db_session,
        request(),
        user=insufficient,
        feature=ComplianceFeature.purchases,
        now=now,
    )
    assert not denied.allowed
    assert denied.jurisdiction == "GB"
    assert denied.code == "AGE_ASSURANCE_INSUFFICIENT"
    assert denied.action == "VERIFY_AGE"


@pytest.mark.asyncio
async def test_current_country_authorities_conflict_and_historical_verification_is_fallback(
    db_session,
):
    now = datetime.now(UTC)
    await _publish_policy(db_session, country="GB", effective_from=now)
    user = await _user(db_session, "country-evidence@example.test", country_code="PT")
    await _age_record(
        db_session,
        user=user,
        country_code="GB",
        assurance=AgeAssuranceLevel.medium,
        expires_at=now + timedelta(days=30),
    )
    for matching_signals in (
        JurisdictionSignals(),
        JurisdictionSignals(trusted_proxy_country="PT", account_country=None),
        JurisdictionSignals(kyc_country="PT", account_country=None),
        JurisdictionSignals(billing_country="PT", account_country=None),
    ):
        decision = await resolve_compliance_decision(
            db_session,
            user=user,
            feature=ComplianceFeature.platform_access,
            signals=matching_signals,
            now=now,
        )
        assert decision.allowed
        assert decision.jurisdiction == "PT"
        assert not decision.country_conflict

    for conflicting_signals in (
        JurisdictionSignals(trusted_proxy_country="GB"),
        JurisdictionSignals(kyc_country="GB"),
        JurisdictionSignals(billing_country="GB"),
    ):
        decision = await resolve_compliance_decision(
            db_session,
            user=user,
            feature=ComplianceFeature.platform_access,
            signals=conflicting_signals,
            now=now,
        )
        assert not decision.allowed
        assert decision.code == "COUNTRY_SIGNAL_CONFLICT"
        assert decision.country_conflict

    provenance_only = await _user(db_session, "provider-provenance@example.test")
    await _age_record(
        db_session,
        user=provenance_only,
        country_code="GB",
        assurance=AgeAssuranceLevel.medium,
        expires_at=now + timedelta(days=30),
    )
    fallback = await resolve_compliance_decision(
        db_session,
        user=provenance_only,
        feature=ComplianceFeature.platform_access,
        now=now,
    )
    assert fallback.allowed
    assert fallback.jurisdiction == "GB"
    assert not fallback.country_conflict

    independent = await _user(db_session, "pairwise-signals@example.test")
    pairwise = await resolve_compliance_decision(
        db_session,
        user=independent,
        feature=ComplianceFeature.platform_access,
        signals=JurisdictionSignals(kyc_country="PT", billing_country="US"),
        now=now,
    )
    assert not pairwise.allowed
    assert pairwise.code == "COUNTRY_SIGNAL_CONFLICT"

    persisted_account = await _user(db_session, "persisted-country@example.test", country_code="PT")
    spoofed_account = await resolve_compliance_decision(
        db_session,
        user=persisted_account,
        feature=ComplianceFeature.platform_access,
        signals=JurisdictionSignals(account_country="US"),
        now=now,
    )
    assert not spoofed_account.allowed
    assert spoofed_account.code == "COUNTRY_SIGNAL_CONFLICT"

    expired_user = await _user(
        db_session, "expired-country-evidence@example.test", country_code="PT"
    )
    await _age_record(
        db_session,
        user=expired_user,
        country_code="GB",
        assurance=AgeAssuranceLevel.medium,
        verified_at=now - timedelta(days=31),
        expires_at=now - timedelta(days=1),
    )
    expired = await resolve_compliance_decision(
        db_session,
        user=expired_user,
        feature=ComplianceFeature.platform_access,
        now=now,
    )
    assert not expired.country_conflict

    failed_attempt_user = await _user(
        db_session, "failed-attempt-country-evidence@example.test", country_code="PT"
    )
    await _age_record(
        db_session,
        user=failed_attempt_user,
        country_code="GB",
        assurance=AgeAssuranceLevel.medium,
        verified_at=now - timedelta(days=1),
        expires_at=now + timedelta(days=30),
        created_at=now - timedelta(minutes=2),
    )
    await _age_record(
        db_session,
        user=failed_attempt_user,
        status=AgeVerificationStatus.failed,
        country_code="PT",
        created_at=now - timedelta(minutes=1),
    )
    failed_attempt = await resolve_compliance_decision(
        db_session,
        user=failed_attempt_user,
        feature=ComplianceFeature.platform_access,
        now=now,
    )
    assert failed_attempt.allowed
    assert failed_attempt.jurisdiction == "PT"
    assert not failed_attempt.country_conflict


@pytest.mark.asyncio
async def test_status_exposes_current_adult_media_decision_when_policy_becomes_stronger(
    db_session,
):
    now = datetime.now(UTC)
    await _publish_policy(
        db_session,
        rule_changes={
            "fan_age_verification_required": True,
            "required_assurance_level": AgeAssuranceLevel.medium,
        },
        effective_from=now,
    )
    user = await _user(db_session, "status-reverify@example.test", country_code="PT")
    await _age_record(
        db_session,
        user=user,
        assurance=AgeAssuranceLevel.medium,
        expires_at=now + timedelta(days=30),
    )
    await _publish_policy(
        db_session,
        rule_changes={
            "fan_age_verification_required": True,
            "required_assurance_level": AgeAssuranceLevel.high,
        },
        effective_from=now + timedelta(microseconds=1),
    )
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/compliance/age-verification/status",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
            "scheme": "http",
        }
    )
    status = await age_verification_status(request, (user, None), db_session)
    assert status.fan_age_verification is not None
    assert status.fan_age_verification.status is AgeVerificationStatus.verified
    assert not status.adult_media_decision.allowed
    assert status.adult_media_decision.code == "AGE_ASSURANCE_INSUFFICIENT"
    assert status.adult_media_decision.action == "VERIFY_AGE"


@pytest.mark.asyncio
async def test_strongest_current_valid_verification_is_not_masked_by_newer_weaker_result(
    db_session,
):
    now = datetime.now(UTC)
    await _publish_policy(
        db_session,
        rule_changes={
            "fan_age_verification_required": True,
            "required_assurance_level": AgeAssuranceLevel.high,
        },
        effective_from=now,
    )
    user = await _user(db_session, "strongest-evidence@example.test", country_code="PT")
    await _age_record(
        db_session,
        user=user,
        assurance=AgeAssuranceLevel.high,
        expires_at=now + timedelta(days=15),
        created_at=now - timedelta(minutes=2),
    )
    await _age_record(
        db_session,
        user=user,
        assurance=AgeAssuranceLevel.medium,
        expires_at=now + timedelta(days=30),
        created_at=now - timedelta(minutes=1),
    )

    decision = await resolve_compliance_decision(
        db_session,
        user=user,
        feature=ComplianceFeature.purchases,
        now=now,
    )

    assert decision.allowed
    assert decision.achieved_assurance_level is AgeAssuranceLevel.high
    assert decision.verification_expires_at == now + timedelta(days=15)


@pytest.mark.asyncio
async def test_creator_eligibility_honors_separate_identity_age_and_payout_policy(db_session):
    now = datetime.now(UTC)
    await _publish_policy(
        db_session,
        rule_changes={
            "creator_identity_required": True,
            "creator_age_verification_required": False,
            "payout_kyc_required": True,
            "reverify_after_days": 30,
        },
        effective_from=now,
    )
    user = await _user(db_session, "creator-policy@example.test", country_code="PT")
    profile = CreatorProfile(
        user_id=user.id,
        country_code="PT",
        status=CreatorStatus.pending_review,
        is_public=False,
    )
    db_session.add(profile)
    await db_session.flush()
    db_session.add(
        CreatorVerification(
            creator_profile_id=profile.id,
            provider="test-kyc",
            provider_reference=f"creator-{uuid4().hex}",
            status=VerificationStatus.verified,
            identity_verified=True,
            adult_verified=False,
            country_code="PT",
            verified_at=now,
            expires_at=now + timedelta(days=90),
        )
    )
    await db_session.flush()
    identity_only = await resolve_creator_compliance_eligibility(
        db_session, profile=profile, now=now
    )
    assert identity_only.identity_allowed
    assert identity_only.age_allowed
    assert identity_only.public_allowed
    assert identity_only.payout_kyc_satisfied
    assert not identity_only.payout_allowed
    assert identity_only.payout_code == "PAYOUT_NOT_CONFIGURED"
    assert identity_only.verification_expires_at == now + timedelta(days=30)

    await _publish_policy(
        db_session,
        rule_changes={
            "creator_identity_required": True,
            "creator_age_verification_required": True,
            "payout_kyc_required": True,
            "reverify_after_days": 30,
        },
        effective_from=now + timedelta(microseconds=1),
    )
    stronger = await resolve_creator_compliance_eligibility(
        db_session, profile=profile, now=now + timedelta(seconds=1)
    )
    assert stronger.identity_allowed
    assert not stronger.age_allowed
    assert not stronger.public_allowed
    assert stronger.code == "CREATOR_AGE_VERIFICATION_REQUIRED"
    with pytest.raises(ValueError, match="CREATOR_AGE_VERIFICATION_REQUIRED"):
        await set_status(
            db_session,
            profile,
            CreatorStatus.approved,
            actor_user_id=user.id,
        )
    registry = await db_session.get(CountryRegistry, "PT")
    assert registry is not None
    registry.enabled = False
    blocked = await resolve_creator_compliance_eligibility(
        db_session, profile=profile, now=now + timedelta(seconds=1)
    )
    assert not blocked.identity_allowed
    assert not blocked.age_allowed
    assert not blocked.public_allowed
    assert not blocked.payout_allowed
    assert blocked.code == "CREATOR_JURISDICTION_BLOCKED"


@pytest.mark.asyncio
async def test_creator_profile_country_cannot_replace_account_or_current_kyc_authority(
    db_session,
):
    now = datetime.now(UTC)
    user = await _user(db_session, "creator-country-authority@example.test", country_code="PT")
    profile = CreatorProfile(
        user_id=user.id,
        country_code="GB",  # Display location is deliberately user editable.
        status=CreatorStatus.pending_verification,
        is_public=False,
    )
    db_session.add(profile)
    await db_session.flush()

    account_authority = await resolve_creator_compliance_eligibility(
        db_session, profile=profile, now=now
    )
    assert account_authority.jurisdiction == "PT"
    assert account_authority.code == "CREATOR_COMPLIANCE_ALLOWED"

    normalized = await development_verify(db_session, profile, True, user.id)
    assert normalized.country_code == "PT"

    # A current KYC result is independent trusted evidence. A mismatch with the
    # persisted account country fails closed instead of selecting either policy.
    normalized.country_code = "GB"
    await db_session.flush()
    conflict = await resolve_creator_compliance_eligibility(db_session, profile=profile, now=now)
    assert conflict.code == "CREATOR_COUNTRY_CONFLICT"
    assert not conflict.public_allowed


@pytest.mark.asyncio
async def test_reverification_grace_never_extends_provider_expiry_or_revocation(db_session):
    now = datetime.now(UTC)
    await _publish_policy(
        db_session,
        rule_changes={
            "fan_age_verification_required": True,
            "required_assurance_level": AgeAssuranceLevel.medium,
            "reverify_after_days": 30,
            "grace_period_days": 5,
        },
        effective_from=now,
    )
    user = await _user(db_session, "grace@example.test", country_code="PT")
    record = await _age_record(
        db_session,
        user=user,
        verified_at=now - timedelta(days=32),
        expires_at=now + timedelta(days=10),
    )
    in_grace = await resolve_compliance_decision(
        db_session,
        user=user,
        feature=ComplianceFeature.purchases,
        now=now,
    )
    assert in_grace.allowed
    assert in_grace.verification_expires_at == record.verified_at + timedelta(days=35)

    after_grace = await resolve_compliance_decision(
        db_session,
        user=user,
        feature=ComplianceFeature.purchases,
        now=now + timedelta(days=4),
    )
    assert not after_grace.allowed
    assert after_grace.code == "AGE_VERIFICATION_EXPIRED"

    provider_expired_user = await _user(
        db_session, "provider-expired@example.test", country_code="PT"
    )
    await _age_record(
        db_session,
        user=provider_expired_user,
        verified_at=now - timedelta(days=10),
        expires_at=now - timedelta(seconds=1),
    )
    provider_expired = await resolve_compliance_decision(
        db_session,
        user=provider_expired_user,
        feature=ComplianceFeature.purchases,
        now=now,
    )
    assert not provider_expired.allowed
    assert provider_expired.code == "AGE_VERIFICATION_EXPIRED"

    revoked_user = await _user(db_session, "revoked@example.test", country_code="PT")
    await _age_record(
        db_session,
        user=revoked_user,
        expires_at=now + timedelta(days=20),
        created_at=now - timedelta(minutes=2),
    )
    await _age_record(
        db_session,
        user=revoked_user,
        status=AgeVerificationStatus.revoked,
        assurance=AgeAssuranceLevel.medium,
        expires_at=now + timedelta(days=20),
        created_at=now,
    )
    revoked = await resolve_compliance_decision(
        db_session,
        user=revoked_user,
        feature=ComplianceFeature.purchases,
        now=now,
    )
    assert not revoked.allowed
    assert revoked.code == "AGE_VERIFICATION_REVOKED"

    failed_reverification = await start_age_verification(
        db_session,
        user=revoked_user,
        country_code="PT",
    )
    pending_after_revocation = await resolve_compliance_decision(
        db_session,
        user=revoked_user,
        feature=ComplianceFeature.purchases,
        now=now,
    )
    assert not pending_after_revocation.allowed
    assert pending_after_revocation.code == "AGE_VERIFICATION_REVOKED"
    assert (
        await current_verification_country(
            db_session,
            user=revoked_user,
            anonymous_session_secret=None,
            now=now,
        )
        is None
    )
    await complete_browser_callback(
        db_session,
        provider_name="test",
        state=_state_from_authorization_url(failed_reverification.authorization_url),
        code="rejected",
    )
    failed_after_revocation = await resolve_compliance_decision(
        db_session,
        user=revoked_user,
        feature=ComplianceFeature.purchases,
        now=now,
    )
    assert not failed_after_revocation.allowed
    assert failed_after_revocation.code == "AGE_VERIFICATION_REVOKED"

    successful_reverification = await start_age_verification(
        db_session,
        user=revoked_user,
        country_code="PT",
    )
    await complete_browser_callback(
        db_session,
        provider_name="test",
        state=_state_from_authorization_url(successful_reverification.authorization_url),
        code="approved",
    )
    restored = await resolve_compliance_decision(
        db_session,
        user=revoked_user,
        feature=ComplianceFeature.purchases,
    )
    assert restored.allowed
    assert (
        await current_verification_country(
            db_session,
            user=revoked_user,
            anonymous_session_secret=None,
            now=datetime.now(UTC),
        )
        == "PT"
    )


@pytest.mark.asyncio
async def test_latest_revoked_at_is_the_tombstone_and_only_strictly_later_success_restores(
    db_session,
):
    now = datetime.now(UTC)
    await _publish_policy(
        db_session,
        rule_changes={
            "fan_age_verification_required": True,
            "required_assurance_level": AgeAssuranceLevel.medium,
            "reverify_after_days": None,
        },
        effective_from=now - timedelta(hours=1),
    )
    user = await _user(db_session, "revocation-chronology@example.test", country_code="PT")
    await _age_record(
        db_session,
        user=user,
        verified_at=now - timedelta(minutes=20),
        expires_at=now + timedelta(days=30),
        created_at=now - timedelta(minutes=20),
    )
    earlier_tombstone = await _age_record(
        db_session,
        user=user,
        status=AgeVerificationStatus.revoked,
        expires_at=now + timedelta(days=30),
        created_at=now - timedelta(minutes=10),
    )
    earlier_tombstone.revoked_at = now - timedelta(minutes=15)
    latest_tombstone = await _age_record(
        db_session,
        user=user,
        status=AgeVerificationStatus.revoked,
        expires_at=now + timedelta(days=30),
        created_at=now - timedelta(minutes=12),
    )
    latest_tombstone.revoked_at = now - timedelta(minutes=5)
    await _age_record(
        db_session,
        user=user,
        verified_at=now - timedelta(minutes=6),
        expires_at=now + timedelta(days=30),
        created_at=now - timedelta(minutes=4),
    )
    pending = await _age_record(
        db_session,
        user=user,
        status=AgeVerificationStatus.pending,
        expires_at=now + timedelta(days=30),
        created_at=now - timedelta(minutes=3),
    )
    pending.verified_at = now - timedelta(minutes=2)
    failed = await _age_record(
        db_session,
        user=user,
        status=AgeVerificationStatus.failed,
        expires_at=now + timedelta(days=30),
        created_at=now - timedelta(minutes=2),
    )
    failed.verified_at = now - timedelta(minutes=1)
    await db_session.flush()

    denied = await resolve_compliance_decision(
        db_session,
        user=user,
        feature=ComplianceFeature.purchases,
        now=now,
    )
    assert not denied.allowed
    assert denied.code == "AGE_VERIFICATION_REVOKED"
    assert (
        await current_verification_country(
            db_session,
            user=user,
            anonymous_session_secret=None,
            now=now,
        )
        is None
    )

    await _age_record(
        db_session,
        user=user,
        verified_at=latest_tombstone.revoked_at,
        expires_at=now + timedelta(days=30),
        created_at=now - timedelta(seconds=30),
    )
    equal_cutoff = await resolve_compliance_decision(
        db_session,
        user=user,
        feature=ComplianceFeature.purchases,
        now=now,
    )
    assert not equal_cutoff.allowed
    assert equal_cutoff.code == "AGE_VERIFICATION_REVOKED"

    await _age_record(
        db_session,
        user=user,
        verified_at=latest_tombstone.revoked_at + timedelta(microseconds=1),
        expires_at=now + timedelta(days=30),
        created_at=now - timedelta(seconds=15),
    )
    restored = await resolve_compliance_decision(
        db_session,
        user=user,
        feature=ComplianceFeature.purchases,
        now=now,
    )
    assert restored.allowed
    assert (
        await current_verification_country(
            db_session,
            user=user,
            anonymous_session_secret=None,
            now=now,
        )
        == "PT"
    )


@pytest.mark.asyncio
async def test_only_current_self_attestation_can_satisfy_explicit_self_attested_policy(db_session):
    now = datetime.now(UTC)
    user = await _user(db_session, "stale-attestation@example.test", country_code="PT")
    user.adult_attested_at = now
    user.adult_attestation_version = "stale-policy"
    stale = await resolve_compliance_decision(
        db_session,
        user=user,
        feature=ComplianceFeature.adult_media,
        adult_restricted=True,
        now=now,
    )
    assert not stale.allowed
    assert stale.code == "AGE_VERIFICATION_REQUIRED"

    user.adult_attestation_version = Settings().adult_attestation_version
    current = await resolve_compliance_decision(
        db_session,
        user=user,
        feature=ComplianceFeature.adult_media,
        adult_restricted=True,
        now=now,
    )
    assert current.allowed
    assert current.achieved_assurance_level is AgeAssuranceLevel.self_attested


@pytest.mark.asyncio
async def test_account_self_attestation_obeys_policy_boundary_and_cannot_elevate_assurance(
    db_session,
):
    now = datetime.now(UTC)
    await _publish_policy(
        db_session,
        rule_changes={
            "fan_age_verification_required": True,
            "required_assurance_level": AgeAssuranceLevel.self_attested,
            "reverify_after_days": 30,
        },
        effective_from=now,
    )
    user = await _user(db_session, "bounded-attestation@example.test", country_code="PT")
    user.adult_attestation_version = Settings().adult_attestation_version
    user.adult_attested_at = now - timedelta(days=30) + timedelta(seconds=1)
    before = await resolve_compliance_decision(
        db_session,
        user=user,
        feature=ComplianceFeature.adult_media,
        adult_restricted=True,
        now=now,
    )
    assert before.allowed
    assert before.verification_expires_at == user.adult_attested_at + timedelta(days=30)

    boundary = user.adult_attested_at + timedelta(days=30)
    at_boundary = await resolve_compliance_decision(
        db_session,
        user=user,
        feature=ComplianceFeature.adult_media,
        adult_restricted=True,
        now=boundary,
    )
    assert not at_boundary.allowed
    assert at_boundary.code == "AGE_VERIFICATION_REQUIRED"
    after = await resolve_compliance_decision(
        db_session,
        user=user,
        feature=ComplianceFeature.adult_media,
        adult_restricted=True,
        now=boundary + timedelta(microseconds=1),
    )
    assert not after.allowed

    user.adult_attested_at = now
    await _publish_policy(
        db_session,
        rule_changes={
            "fan_age_verification_required": True,
            "required_assurance_level": AgeAssuranceLevel.medium,
            "reverify_after_days": 30,
        },
        effective_from=now + timedelta(microseconds=1),
    )
    stronger = await resolve_compliance_decision(
        db_session,
        user=user,
        feature=ComplianceFeature.adult_media,
        adult_restricted=True,
        now=now + timedelta(seconds=1),
    )
    assert not stronger.allowed
    assert stronger.code == "AGE_ASSURANCE_INSUFFICIENT"
    assert stronger.achieved_assurance_level is AgeAssuranceLevel.self_attested


@pytest.mark.asyncio
async def test_country_signal_conflict_fails_closed(db_session):
    decision = await resolve_compliance_decision(
        db_session,
        user=None,
        feature=ComplianceFeature.platform_access,
        signals=JurisdictionSignals(billing_country="PT", request_country="US"),
    )
    assert not decision.allowed
    assert decision.code == "COUNTRY_SIGNAL_CONFLICT"
    assert decision.country_conflict


def test_trusted_country_header_requires_configured_proxy(monkeypatch):
    import app.compliance.http as compliance_http

    settings = Settings(trusted_country_header="x-country", trusted_proxy_cidrs="10.0.0.0/8")
    monkeypatch.setattr(compliance_http, "get_settings", lambda: settings)
    trusted = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-country", b"us")],
            "query_string": b"",
            "client": ("10.1.2.3", 1234),
            "server": ("test", 80),
            "scheme": "http",
        }
    )
    untrusted = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-country", b"us")],
            "query_string": b"",
            "client": ("203.0.113.5", 1234),
            "server": ("test", 80),
            "scheme": "http",
        }
    )
    assert request_country_from_trusted_proxy(trusted) == "US"
    assert request_country_from_trusted_proxy(untrusted) is None
    merged = jurisdiction_signals_from_request(
        trusted,
        user=None,
        signals=JurisdictionSignals(selected_country="PT"),
    )
    assert merged.trusted_proxy_country == "US"
    assert merged.selected_country == "PT"
    assert (
        resolve_request_jurisdiction(
            trusted,
            user=None,
            signals=JurisdictionSignals(selected_country="PT"),
        )
        is None
    )
    assert (
        resolve_request_jurisdiction(
            untrusted,
            user=None,
            signals=JurisdictionSignals(selected_country="US"),
        )
        == "US"
    )


def test_production_never_promotes_an_untrusted_country_selection_to_authority():
    selected_us = JurisdictionSignals(selected_country="US")
    assert resolve_jurisdiction_candidates(
        selected_us,
        fallback_country="PT",
        allow_untrusted_selection=False,
    ) == ("PT", "US")
    assert resolve_jurisdiction_candidates(
        JurisdictionSignals(selected_country="PT"),
        fallback_country="PT",
        allow_untrusted_selection=False,
    ) == ("PT",)
    assert (
        resolve_jurisdiction_candidates(
            selected_us,
            fallback_country=None,
            allow_untrusted_selection=False,
        )
        == ()
    )


@pytest.mark.asyncio
async def test_request_jurisdiction_prefers_current_account_and_proxy_over_historical_provenance(
    db_session, monkeypatch
):
    import app.compliance.http as compliance_http

    now = datetime.now(UTC)
    settings = Settings(
        environment="test",
        trusted_country_header="x-country",
        trusted_proxy_cidrs="10.0.0.0/8",
    )
    monkeypatch.setattr(compliance_http, "get_settings", lambda: settings)

    account_conflict = await _user(
        db_session, "evidence-account-conflict@example.test", country_code="PT"
    )
    await _age_record(
        db_session,
        user=account_conflict,
        country_code="GB",
        expires_at=now + timedelta(days=30),
    )
    untrusted_request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "client": ("203.0.113.5", 1234),
            "server": ("test", 80),
            "scheme": "http",
        }
    )
    assert (
        await resolve_request_jurisdiction_with_evidence(
            db_session,
            untrusted_request,
            user=account_conflict,
            now=now,
        )
        == "PT"
    )

    proxy_conflict = await _user(
        db_session, "evidence-proxy-conflict@example.test", country_code=None
    )
    await _age_record(
        db_session,
        user=proxy_conflict,
        country_code="GB",
        expires_at=now + timedelta(days=30),
    )
    trusted_pt = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-country", b"PT")],
            "query_string": b"",
            "client": ("10.1.2.3", 1234),
            "server": ("test", 80),
            "scheme": "http",
        }
    )
    assert (
        await resolve_request_jurisdiction_with_evidence(
            db_session,
            trusted_pt,
            user=proxy_conflict,
            now=now,
        )
        == "PT"
    )
    trusted_gb = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-country", b"GB")],
            "query_string": b"",
            "client": ("10.1.2.3", 1234),
            "server": ("test", 80),
            "scheme": "http",
        }
    )
    assert (
        await resolve_request_jurisdiction_with_evidence(
            db_session,
            trusted_gb,
            user=proxy_conflict,
            now=now,
        )
        == "GB"
    )


def test_production_requires_fallback_even_when_trusted_geoip_is_configured():
    settings = Settings(
        environment="production",
        session_secret="s" * 40,
        notification_webhook_secret="n" * 40,
        payment_webhook_secret="p" * 40,
        cookie_secure=True,
        api_docs_enabled=False,
        livekit_webhook_configured=True,
        release_sha="0123456789abcdef",
        web_origin="https://example.test",
        api_origin="https://api.example.test",
        database_url=(
            "postgresql+asyncpg://fan_prod:database-password-with-32-characters"
            "@db.internal:5432/fanbackstage?ssl=verify-full"
        ),
        redis_url="rediss://:redis-password-with-32-characters@redis.internal:6380/0",
        smtp_host="smtp.example.test",
        smtp_username="fanbackstage-smtp-user",
        smtp_password="smtp-password-with-at-least-32-characters",
        smtp_start_tls=True,
        storage_endpoint_url="https://storage.example.test",
        storage_access_key="production-storage-key",
        storage_secret_key="production-storage-secret-with-32-characters",
        livekit_url="wss://livekit.example.test",
        livekit_api_key="livekit-production-key",
        livekit_api_secret="livekit-production-secret-with-24-characters",
        age_assurance_provider="verifymyage",
        verifymyage_environment="production",
        verifymyage_client_id="configured-client",
        verifymyage_client_secret="configured-secret",
        internal_country_handoff_secret="h" * 40,
        compliance_fallback_country="",
        trusted_country_header="x-country",
        trusted_proxy_cidrs="10.0.0.0/16",
    )
    with pytest.raises(RuntimeError, match="reviewed compliance fallback country"):
        settings.validate_production()


@pytest.mark.asyncio
async def test_test_provider_supports_independent_results_expiry_and_outage_recovery(db_session):
    user = await _user(db_session, "provider-outcomes@example.test", country_code="PT")
    first = await start_age_verification(db_session, user=user, country_code="PT")
    second = await start_age_verification(db_session, user=user, country_code="PT")
    first_done = await complete_browser_callback(
        db_session,
        provider_name="test",
        state=_state_from_authorization_url(first.authorization_url),
        code="approved",
    )
    second_done = await complete_browser_callback(
        db_session,
        provider_name="test",
        state=_state_from_authorization_url(second.authorization_url),
        code="approved",
    )
    assert first_done.record.provider_verification_id != second_done.record.provider_verification_id

    expired = await start_age_verification(db_session, user=user, country_code="PT")
    expired_done = await complete_browser_callback(
        db_session,
        provider_name="test",
        state=_state_from_authorization_url(expired.authorization_url),
        code="expired",
    )
    assert expired_done.record.status is AgeVerificationStatus.expired

    failed = await start_age_verification(db_session, user=user, country_code="PT")
    failed_done = await complete_browser_callback(
        db_session,
        provider_name="test",
        state=_state_from_authorization_url(failed.authorization_url),
        code="denied",
    )
    assert failed_done.record.status is AgeVerificationStatus.failed
    assert failed_done.record.failed_at is not None

    unavailable = await start_age_verification(db_session, user=user, country_code="PT")
    state = _state_from_authorization_url(unavailable.authorization_url)
    with pytest.raises(AgeVerificationError) as caught:
        await complete_browser_callback(
            db_session,
            provider_name="test",
            state=state,
            code="unavailable",
        )
    assert caught.value.retryable
    recovered = await complete_browser_callback(
        db_session,
        provider_name="test",
        state=state,
        code="approved",
    )
    assert recovered.record.status is AgeVerificationStatus.verified


@pytest.mark.asyncio
async def test_disabled_country_cannot_start_provider_or_create_verification_record(
    db_session, monkeypatch
):
    registry = await db_session.get(CountryRegistry, "PT")
    assert registry is not None
    registry.enabled = False
    user = await _user(db_session, "blocked-country@example.test", country_code="PT")

    def unexpected_provider(*_args, **_kwargs):
        raise AssertionError("provider adapter must not run for a disabled country")

    monkeypatch.setattr(
        "app.compliance.age_verification.get_age_verification_provider",
        unexpected_provider,
    )
    with pytest.raises(AgeVerificationError) as caught:
        await start_age_verification(db_session, user=user, country_code="PT")
    assert caught.value.code == "JURISDICTION_BLOCKED"
    assert (await db_session.scalar(select(func.count()).select_from(AgeVerificationRecord))) == 0


@pytest.mark.asyncio
async def test_provider_result_without_expiry_uses_policy_bound_and_never_verifies_forever(
    db_session, monkeypatch
):
    class NoExpiryProvider:
        sequence = 0

        async def create_verification_session(self, request):
            return ProviderStartResult(
                authorization_url=(
                    "https://provider.example/authorize?"
                    + urlencode({"state": request.state, "code": "approved"})
                )
            )

        async def exchange_browser_callback(self, code):
            assert code == "approved"
            self.sequence += 1
            return ProviderVerificationResult(
                provider_verification_id=f"no-expiry-{self.sequence}",
                status=AgeVerificationStatus.verified,
                age_verified=True,
                achieved_assurance_level=AgeAssuranceLevel.medium,
                achieved_minimum_age=18,
                verified_at=datetime.now(UTC),
                expires_at=None,
            )

    provider = NoExpiryProvider()
    monkeypatch.setattr(
        "app.compliance.age_verification.get_age_verification_provider",
        lambda _name, settings=None: provider,
    )
    await _publish_policy(
        db_session,
        rule_changes={
            "age_provider": "verifymyage",
            "fan_age_verification_required": True,
            "required_assurance_level": AgeAssuranceLevel.medium,
            "reverify_after_days": 10,
            "grace_period_days": 3,
        },
    )
    user = await _user(db_session, "finite-vma@example.test", country_code="PT")
    bounded = await start_age_verification(db_session, user=user, country_code="PT")
    bounded_done = await complete_browser_callback(
        db_session,
        provider_name="verifymyage",
        state=_state_from_authorization_url(bounded.authorization_url),
        code="approved",
    )
    assert bounded_done.record.status is AgeVerificationStatus.verified
    assert bounded_done.record.verified_at is not None
    assert bounded_done.record.expires_at == bounded_done.record.verified_at + timedelta(days=13)
    grace_now = bounded_done.record.verified_at + timedelta(days=11)
    grace_decision = await resolve_compliance_decision(
        db_session,
        user=user,
        feature=ComplianceFeature.adult_media,
        adult_restricted=True,
        now=grace_now,
    )
    assert grace_decision.allowed
    assert grace_decision.verification_expires_at == bounded_done.record.expires_at
    assert await expire_due_verifications(db_session, now=grace_now) == 0
    assert (
        await expire_due_verifications(
            db_session,
            now=bounded_done.record.verified_at + timedelta(days=14),
        )
        == 1
    )
    assert bounded_done.record.status is AgeVerificationStatus.expired
    expiry_audit = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == "compliance.age_verification_expired",
            AuditEvent.target_id == str(bounded_done.record.id),
        )
    )
    assert expiry_audit is not None
    assert expiry_audit.metadata_json["before"] == "verified"
    assert expiry_audit.metadata_json["after"] == "expired"

    await _publish_policy(
        db_session,
        rule_changes={
            "age_provider": "verifymyage",
            "fan_age_verification_required": True,
            "required_assurance_level": AgeAssuranceLevel.medium,
            "reverify_after_days": None,
        },
    )
    unbounded = await start_age_verification(db_session, user=user, country_code="PT")
    unbounded_done = await complete_browser_callback(
        db_session,
        provider_name="verifymyage",
        state=_state_from_authorization_url(unbounded.authorization_url),
        code="approved",
    )
    assert unbounded_done.record.status is AgeVerificationStatus.review_required
    assert unbounded_done.record.failure_reason_code == "NORMALIZED_VALIDITY_INCOMPLETE"
    assert unbounded_done.record.verified_at is None
    assert unbounded_done.record.expires_at is None


@pytest.mark.asyncio
async def test_expiry_notifications_follow_canonical_evidence_not_superseded_attempts(db_session):
    now = datetime.now(UTC)
    await _publish_policy(
        db_session,
        rule_changes={
            "fan_age_verification_required": True,
            "required_assurance_level": AgeAssuranceLevel.medium,
            "reverify_after_days": None,
            "grace_period_days": 0,
        },
        effective_from=now,
    )
    superseded_user = await _user(
        db_session,
        "superseded-expiry@example.test",
        country_code="PT",
    )
    await _age_record(
        db_session,
        user=superseded_user,
        assurance=AgeAssuranceLevel.medium,
        expires_at=now + timedelta(days=2),
        created_at=now - timedelta(days=2),
    )
    await _age_record(
        db_session,
        user=superseded_user,
        assurance=AgeAssuranceLevel.medium,
        expires_at=now + timedelta(days=30),
        created_at=now - timedelta(days=1),
    )
    expiring_user = await _user(db_session, "current-expiry@example.test", country_code="PT")
    await _age_record(
        db_session,
        user=expiring_user,
        assurance=AgeAssuranceLevel.medium,
        expires_at=now + timedelta(days=2),
    )

    assert await notify_expiring_verifications(db_session, now=now, within_days=7) == 1
    intents = (
        await db_session.scalars(
            select(NotificationIntent).where(
                NotificationIntent.notification_type == "AGE_VERIFICATION_EXPIRING"
            )
        )
    ).all()
    assert [intent.recipient_user_id for intent in intents] == [expiring_user.id]


@pytest.mark.asyncio
async def test_stale_authenticated_and_anonymous_callbacks_fail_before_provider_exchange(
    db_session,
):
    now = datetime.now(UTC)
    user = await _user(db_session, "stale-callback@example.test", country_code="PT")
    authenticated = await start_age_verification(
        db_session,
        user=user,
        country_code="PT",
        now=now,
    )
    authenticated.record.initiated_at = now - timedelta(hours=25)
    with pytest.raises(AgeVerificationError) as authenticated_error:
        await complete_browser_callback(
            db_session,
            provider_name="test",
            state=_state_from_authorization_url(authenticated.authorization_url),
            code="approved",
            now=now,
        )
    assert authenticated_error.value.code == "CALLBACK_STATE_EXPIRED"
    assert authenticated.record.status is AgeVerificationStatus.failed
    assert authenticated.record.state_consumed_at == now

    anonymous = await start_age_verification(
        db_session,
        user=None,
        country_code="PT",
        now=now,
    )
    # The anonymous session is a separate, mandatory callback authority. Force
    # it stale while the OAuth state itself remains within its bounded TTL.
    anonymous_session = await db_session.get(
        AnonymousComplianceSession, anonymous.record.anonymous_session_id
    )
    assert anonymous_session is not None
    anonymous_session.expires_at = now - timedelta(seconds=1)
    with pytest.raises(AgeVerificationError) as anonymous_error:
        await complete_browser_callback(
            db_session,
            provider_name="test",
            state=_state_from_authorization_url(anonymous.authorization_url),
            code="approved",
            now=now,
        )
    assert anonymous_error.value.code == "ANONYMOUS_SESSION_EXPIRED"
    assert anonymous.record.status is AgeVerificationStatus.failed
    expired_events = (
        await db_session.scalars(
            select(AuditEvent).where(
                AuditEvent.event_type == "compliance.age_verification_callback_expired"
            )
        )
    ).all()
    assert {event.target_id for event in expired_events} == {
        str(authenticated.record.id),
        str(anonymous.record.id),
    }
    assert {event.metadata_json["reason_code"] for event in expired_events} == {
        "CALLBACK_STATE_EXPIRED",
        "ANONYMOUS_SESSION_EXPIRED",
    }


@pytest.mark.asyncio
async def test_concurrent_duplicate_callback_is_idempotent(db_session):
    user = await _user(db_session, "callback-replay@example.test", country_code="PT")
    started = await start_age_verification(db_session, user=user, country_code="PT")
    state = _state_from_authorization_url(started.authorization_url)
    await db_session.commit()

    async def deliver():
        async with SessionLocal() as session:
            result = await complete_browser_callback(
                session,
                provider_name="test",
                state=state,
                code="approved",
            )
            await session.commit()
            return result.replayed, result.record.id

    results = await asyncio.gather(deliver(), deliver())
    assert sorted(replayed for replayed, _ in results) == [False, True]
    assert len({record_id for _, record_id in results}) == 1


@pytest.mark.asyncio
async def test_provider_reference_reuse_is_terminally_rejected_and_audited(db_session, monkeypatch):
    class ReusedReferenceProvider:
        async def create_verification_session(self, request):
            return ProviderStartResult(
                authorization_url=(
                    "https://provider.example/authorize?"
                    + urlencode({"state": request.state, "code": "approved"})
                )
            )

        async def exchange_browser_callback(self, _code):
            now = datetime.now(UTC)
            return ProviderVerificationResult(
                provider_verification_id="shared-provider-reference",
                status=AgeVerificationStatus.verified,
                age_verified=True,
                achieved_assurance_level=AgeAssuranceLevel.medium,
                achieved_minimum_age=18,
                verified_at=now,
                expires_at=now + timedelta(days=30),
            )

    provider = ReusedReferenceProvider()
    monkeypatch.setattr(
        "app.compliance.age_verification.get_age_verification_provider",
        lambda _name, settings=None: provider,
    )
    first_user = await _user(db_session, "provider-reference-one@example.test", country_code="PT")
    second_user = await _user(db_session, "provider-reference-two@example.test", country_code="PT")
    first = await start_age_verification(db_session, user=first_user, country_code="PT")
    second = await start_age_verification(db_session, user=second_user, country_code="PT")
    await complete_browser_callback(
        db_session,
        provider_name="test",
        state=_state_from_authorization_url(first.authorization_url),
        code="approved",
    )

    with pytest.raises(AgeVerificationError) as caught:
        await complete_browser_callback(
            db_session,
            provider_name="test",
            state=_state_from_authorization_url(second.authorization_url),
            code="approved",
        )

    assert caught.value.code == "PROVIDER_REFERENCE_REUSED"
    assert second.record.status is AgeVerificationStatus.review_required
    assert second.record.provider_verification_id is None
    assert second.record.state_consumed_at is not None
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(AgeVerificationRecord)
            .where(
                AgeVerificationRecord.provider == "test",
                AgeVerificationRecord.provider_verification_id == "shared-provider-reference",
            )
        )
        == 1
    )
    audit = await db_session.scalar(
        select(AuditEvent)
        .where(
            AuditEvent.event_type == "compliance.age_verification_callback_failed",
            AuditEvent.target_id == str(second.record.id),
        )
        .order_by(AuditEvent.created_at.desc())
    )
    assert audit is not None
    assert audit.metadata_json["reason_code"] == "PROVIDER_REFERENCE_REUSED"


@pytest.mark.asyncio
async def test_anonymous_session_is_expiry_bounded_and_cannot_be_reassigned(db_session):
    started = await start_age_verification(db_session, user=None, country_code="PT")
    assert started.anonymous_session_secret is not None
    completed = await complete_browser_callback(
        db_session,
        provider_name="test",
        state=_state_from_authorization_url(started.authorization_url),
        code="approved",
    )
    assert completed.anonymous_session_expires_at is not None
    assert completed.record.expires_at is not None
    assert completed.anonymous_session_expires_at <= completed.record.expires_at

    first_user = await _user(db_session, "attach-one@example.test", country_code="PT")
    second_user = await _user(db_session, "attach-two@example.test", country_code="PT")
    await attach_anonymous_session(
        db_session,
        anonymous_session_secret=started.anonymous_session_secret,
        user=first_user,
    )
    with pytest.raises(AgeVerificationError) as caught:
        await attach_anonymous_session(
            db_session,
            anonymous_session_secret=started.anonymous_session_secret,
            user=second_user,
        )
    assert caught.value.code == "SESSION_ALREADY_ATTACHED"

    session_row = await attach_anonymous_session(
        db_session,
        anonymous_session_secret=started.anonymous_session_secret,
        user=first_user,
    )
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            session_row.attached_user_id = second_user.id
            await db_session.flush()
    with pytest.raises(DBAPIError):
        async with db_session.begin_nested():
            completed.record.user_id = second_user.id
            await db_session.flush()


@pytest.mark.asyncio
async def test_anonymous_verified_session_country_precedes_configured_fallback(db_session):
    now = datetime.now(UTC)
    await _publish_policy(
        db_session,
        country="US",
        rule_changes={
            "fan_age_verification_required": True,
            "required_assurance_level": AgeAssuranceLevel.medium,
        },
        effective_from=now,
    )
    started = await start_age_verification(db_session, user=None, country_code="US")
    assert started.anonymous_session_secret is not None
    await complete_browser_callback(
        db_session,
        provider_name="test",
        state=_state_from_authorization_url(started.authorization_url),
        code="approved",
    )
    decision = await resolve_compliance_decision(
        db_session,
        user=None,
        feature=ComplianceFeature.platform_access,
        anonymous_session_secret=started.anonymous_session_secret,
    )
    assert decision.allowed
    assert decision.jurisdiction == "US"


def test_test_adapter_is_blocked_in_production():
    with pytest.raises(ProviderConfigurationError) as caught:
        get_age_verification_provider("test", settings=Settings(environment="production"))
    assert caught.value.code == "TEST_PROVIDER_BLOCKED"


@pytest.mark.asyncio
async def test_verifymyage_oauth_and_diagnostic_contract_normalizes_only_safe_fields():
    requests: list[httpx.Request] = []
    api_key = "diagnostic-api-key"
    api_secret = "diagnostic-api-secret"
    callback_url = (
        "https://api.example.test/api/v1/compliance/age-verification/callback/verifymyage"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/oauth/token":
            assert request.headers["authorization"].startswith("Basic ")
            assert json.loads(request.content) == {"code": "oauth-code"}
            return httpx.Response(200, json={"access_token": "transient-access-token"})
        if request.url.path == "/users/me":
            assert request.url.params["access_token"] == "transient-access-token"
            return httpx.Response(
                200,
                json={
                    "id": "provider-verification-1",
                    "age_verified": True,
                    "threshold": 18,
                    "unpersisted_provider_detail": "must-not-cross-boundary",
                },
            )
        if request.url.path == "/v1/business/allowed-redirects":
            expected = hmac.new(
                api_secret.encode(),
                b"/v1/business/allowed-redirects",
                hashlib.sha256,
            ).hexdigest()
            assert request.headers["authorization"] == f"hmac {api_key}:{expected}"
            return httpx.Response(200, json={"body": [callback_url]})
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = VerifyMyAgeProvider(
            environment="production",
            client_id=api_key,
            client_secret=api_secret,
            client=client,
        )
        started = await provider.create_verification_session(
            ProviderStartRequest(
                country_code="PT",
                state="one-time-state",
                redirect_uri=callback_url,
                user_reference="safe-user-reference",
            )
        )
        authorization = urlsplit(started.authorization_url)
        authorization_params = parse_qs(authorization.query)
        assert authorization.scheme == "https"
        assert authorization.netloc == "oauth.verifymyage.com"
        assert authorization.path == "/oauth/authorize"
        assert authorization_params == {
            "client_id": [api_key],
            "scope": ["adult"],
            "country": ["pt"],
            "redirect_uri": [callback_url],
            "state": ["one-time-state"],
            "user_id": ["safe-user-reference"],
        }
        result = await provider.exchange_browser_callback("oauth-code")
        diagnostic = await provider.get_provider_status(callback_url)

    assert result.provider_verification_id == "provider-verification-1"
    assert result.age_verified
    assert result.achieved_assurance_level is AgeAssuranceLevel.low
    assert result.achieved_minimum_age == 18
    assert not hasattr(result, "access_token")
    assert diagnostic.healthy and diagnostic.allowed_redirect
    assert [request.url.path for request in requests] == [
        "/oauth/token",
        "/users/me",
        "/v1/business/allowed-redirects",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("token_payload", "result_payload", "expected_code"),
    [
        ([], None, "TOKEN_RESPONSE_INCOMPLETE"),
        ({"access_token": "token"}, [], "RESULT_RESPONSE_INCOMPLETE"),
        (
            {"access_token": "token"},
            {"id": "", "age_verified": True, "threshold": 18},
            "RESULT_RESPONSE_INCOMPLETE",
        ),
        (
            {"access_token": "token"},
            {"id": "x" * 256, "age_verified": True, "threshold": 18},
            "RESULT_RESPONSE_INCOMPLETE",
        ),
        (
            {"access_token": "token"},
            {"id": True, "age_verified": True, "threshold": 18},
            "RESULT_RESPONSE_INCOMPLETE",
        ),
        (
            {"access_token": "token"},
            {"id": "subject", "age_verified": True, "threshold": True},
            "RESULT_RESPONSE_INVALID",
        ),
        (
            {"access_token": "token"},
            {"id": "subject", "age_verified": True, "threshold": 0},
            "RESULT_RESPONSE_INVALID",
        ),
        (
            {"access_token": "token"},
            {"id": "subject", "age_verified": True, "threshold": 121},
            "RESULT_RESPONSE_INVALID",
        ),
        (
            {"access_token": "token"},
            {"id": "subject", "age_verified": True, "threshold": None},
            "RESULT_RESPONSE_INVALID",
        ),
    ],
)
async def test_verifymyage_rejects_malformed_normalized_payloads(
    token_payload,
    result_payload,
    expected_code,
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json=token_payload)
        if request.url.path == "/users/me":
            return httpx.Response(200, json=result_payload)
        raise AssertionError(f"Unexpected provider request {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = VerifyMyAgeProvider(
            environment="production",
            client_id="client-id",
            client_secret="client-secret",
            client=client,
        )
        with pytest.raises(ProviderError) as caught:
            await provider.exchange_browser_callback("oauth-code")

    assert caught.value.code == expected_code
    assert not caught.value.retryable


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_code", "retryable"),
    [
        (401, "PROVIDER_CREDENTIALS_REJECTED", False),
        (429, "PROVIDER_UNAVAILABLE", True),
        (503, "PROVIDER_UNAVAILABLE", True),
    ],
)
async def test_verifymyage_classifies_terminal_and_retryable_http_failures(
    status_code,
    expected_code,
    retryable,
):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(status_code, json={"message": "redacted"})
        )
    ) as client:
        provider = VerifyMyAgeProvider(
            environment="production",
            client_id="client-id",
            client_secret="client-secret",
            client=client,
        )
        with pytest.raises(ProviderError) as caught:
            await provider.exchange_browser_callback("oauth-code")

    assert caught.value.code == expected_code
    assert caught.value.retryable is retryable


def test_0037_upgrade_keeps_legacy_adult_evidence_separate_from_identity_kyc(monkeypatch):
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/20260827_0037_compliance_age_jurisdiction.py"
    )
    spec = importlib.util.spec_from_file_location("compliance_migration_0037", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    recorder = _MigrationOperationsRecorder()
    monkeypatch.setattr(migration, "op", recorder)
    monkeypatch.setattr(migration.postgresql.ENUM, "create", lambda *_args, **_kwargs: None)

    migration.upgrade()

    identity_columns = [
        args[1]
        for name, args, _kwargs in recorder.calls
        if name == "add_column"
        and args[0] == "creator_verifications"
        and args[1].name == "identity_verified"
    ]
    assert len(identity_columns) == 1
    assert str(identity_columns[0].server_default.arg) == "false"
    statements = [
        args[0]
        for name, args, _kwargs in recorder.calls
        if name == "execute" and args and isinstance(args[0], str)
    ]
    assert not any("identity_verified = adult_verified" in statement for statement in statements)
    assert any(
        "UPDATE creator_verifications SET verified_at" in statement for statement in statements
    )


def test_0037_downgrade_refuses_account_country_and_explicit_creator_identity_evidence(
    monkeypatch,
):
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/20260827_0037_compliance_age_jurisdiction.py"
    )
    spec = importlib.util.spec_from_file_location("compliance_migration_0037_guard", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    class EvidenceBind:
        statement = ""

        def execute(self, statement):
            self.statement = str(statement)
            return self

        @staticmethod
        def scalar_one() -> bool:
            return True

    evidence_bind = EvidenceBind()
    recorder = _MigrationOperationsRecorder()
    recorder.get_bind = lambda: evidence_bind  # type: ignore[method-assign]
    monkeypatch.setattr(migration, "op", recorder)

    with pytest.raises(RuntimeError, match="Cannot downgrade compliance migration"):
        migration.downgrade()

    normalized_guard = " ".join(evidence_bind.statement.split())
    assert "SELECT 1 FROM users WHERE country_code IS NOT NULL" in normalized_guard
    assert "identity_verified IS TRUE" in normalized_guard
    assert "verified_at IS NOT NULL" in normalized_guard
    assert recorder.calls == []


@pytest.mark.asyncio
async def test_production_readiness_rejects_demo_and_policy_provider_mismatch(db_session):
    ready, reasons = await production_policy_readiness(
        db_session,
        settings=Settings(
            environment="production",
            age_assurance_provider="verifymyage",
            verifymyage_environment="production",
            verifymyage_client_id="configured-client",
            verifymyage_client_secret="configured-secret",
            api_origin="https://api.example.test",
            web_origin="https://example.test",
            cookie_secure=True,
            compliance_fallback_country="PT",
        ),
    )
    assert not ready
    assert "ACTIVE_DEMO_JURISDICTION_POLICY" in reasons
    assert "ACTIVE_POLICY_PROVIDER_MISMATCH" in reasons

    _, missing_fallback_reasons = await production_policy_readiness(
        db_session,
        settings=Settings(
            environment="production",
            age_assurance_provider="verifymyage",
            verifymyage_environment="production",
            verifymyage_client_id="configured-client",
            verifymyage_client_secret="configured-secret",
            api_origin="https://api.example.test",
            web_origin="https://example.test",
            cookie_secure=True,
            compliance_fallback_country="US",
        ),
    )
    assert "FALLBACK_JURISDICTION_NOT_READY" in missing_fallback_reasons


@pytest.mark.asyncio
async def test_staging_compliance_readiness_does_not_auto_pass_unmarked_demo_policy(db_session):
    ready, reasons = await production_policy_readiness(
        db_session,
        settings=Settings(
            environment="staging",
            age_assurance_provider="verifymyage",
            verifymyage_environment="sandbox",
            api_origin="https://api.staging.example.test",
            compliance_fallback_country="PT",
        ),
    )
    assert ready is False
    assert "STAGING_TEMPLATE_POLICY_NOT_EXPLICITLY_MARKED" in reasons
    assert "STAGING_JURISDICTION_POLICY_NOT_EXPLICITLY_MARKED" in reasons


@pytest.mark.asyncio
async def test_legacy_account_without_country_never_inherits_production_fallback(
    db_session, monkeypatch
):
    legacy = await _user(db_session, "legacy-country-migration@example.test", country_code=None)
    settings = Settings(
        environment="production",
        compliance_fallback_country="PT",
    )
    monkeypatch.setattr(compliance_policy, "get_settings", lambda: settings)
    decision = await resolve_compliance_decision(
        db_session,
        user=legacy,
        feature=ComplianceFeature.platform_access,
        signals=JurisdictionSignals(),
    )
    assert not decision.allowed
    assert decision.code == "JURISDICTION_UNRESOLVED"
    assert decision.jurisdiction is None

    _, reasons = await production_policy_readiness(db_session, settings=settings)
    assert "ACCOUNT_JURISDICTION_MIGRATION_REQUIRED" in reasons

    actor, _ = await _actor_and_template(db_session)
    established = await set_account_country(
        db_session,
        user_id=legacy.id,
        country_code="PT",
        actor_user_id=actor.id,
        change_reason="Reviewed legacy account country evidence",
        source="operator_review",
    )
    assert established.country_code == "PT"
    audit = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == "compliance.account_country_changed",
            AuditEvent.target_id == str(legacy.id),
        )
    )
    assert audit is not None
    assert audit.metadata_json["before_country"] == "UNSET"
    assert audit.metadata_json["after_country"] == "PT"


@pytest.mark.asyncio
async def test_country_activation_and_readiness_require_an_effective_policy(db_session):
    actor, _ = await _actor_and_template(db_session)
    assert CountryRegistry.__table__.c.enabled.default.arg is False
    for country in (await db_session.scalars(select(CountryRegistry))).all():
        country.enabled = country.code == "PT"
    await db_session.flush()

    germany = await db_session.get(CountryRegistry, "DE")
    assert germany is not None
    with pytest.raises(CompliancePolicyError, match="reviewed effective policy"):
        await set_country_enabled(
            db_session,
            code="DE",
            enabled=True,
            actor_user_id=actor.id,
            change_reason="Must fail before policy publication",
        )

    # Readiness also detects legacy/direct-SQL configuration that bypassed the
    # command invariant, so health cannot advertise an unusable jurisdiction.
    germany.enabled = True
    await db_session.flush()
    settings = Settings(
        environment="production",
        age_assurance_provider="test",
        api_origin="https://api.example.test",
        web_origin="https://example.test",
        cookie_secure=True,
        compliance_fallback_country="PT",
    )
    _, reasons = await production_policy_readiness(db_session, settings=settings)
    assert "ENABLED_JURISDICTION_POLICY_MISSING" in reasons

    germany.enabled = False
    await _publish_policy(db_session, country="DE")
    enabled = await set_country_enabled(
        db_session,
        code="DE",
        enabled=True,
        actor_user_id=actor.id,
        change_reason="Enable only after reviewed effective policy publication",
    )
    assert enabled.enabled is True
    _, reasons = await production_policy_readiness(db_session, settings=settings)
    assert "ENABLED_JURISDICTION_POLICY_MISSING" not in reasons


@pytest.mark.asyncio
async def test_production_readiness_uses_authoritative_non_demo_successors(db_session):
    now = datetime.now(UTC)
    actor, _, _, _ = await _publish_policy(
        db_session,
        rule_changes={
            "age_provider": "verifymyage",
            "fan_age_verification_required": True,
            "reverify_after_days": 30,
        },
        effective_from=now,
        is_demo=False,
    )
    await create_feature_flag_revision(
        db_session,
        feature=ComplianceFeature.messaging,
        country_scope="PT",
        enabled=False,
        effective_from=now - timedelta(seconds=2),
        effective_until=None,
        actor_user_id=actor.id,
        change_reason="Superseded demo feature revision",
        is_demo=True,
    )
    await create_feature_flag_revision(
        db_session,
        feature=ComplianceFeature.messaging,
        country_scope="PT",
        enabled=True,
        effective_from=now - timedelta(seconds=1),
        effective_until=None,
        actor_user_id=actor.id,
        change_reason="Reviewed production feature successor",
        is_demo=False,
    )

    _, reasons = await production_policy_readiness(
        db_session,
        settings=Settings(
            environment="production",
            age_assurance_provider="verifymyage",
            verifymyage_environment="production",
            verifymyage_client_id="configured-client",
            verifymyage_client_secret="configured-secret",
            api_origin="https://api.example.test",
            web_origin="https://example.test",
            cookie_secure=True,
            compliance_fallback_country="PT",
        ),
        now=now + timedelta(seconds=1),
    )

    assert "ACTIVE_DEMO_JURISDICTION_POLICY" not in reasons
    assert "ACTIVE_DEMO_TEMPLATE_POLICY" not in reasons
    assert "ACTIVE_POLICY_TEMPLATE_DEMO" not in reasons
    assert "ACTIVE_DEMO_FEATURE_FLAG" not in reasons


@pytest.mark.asyncio
async def test_production_readiness_rejects_unbounded_verifymyage_policy(db_session):
    await _publish_policy(
        db_session,
        rule_changes={
            "age_provider": "verifymyage",
            "fan_age_verification_required": True,
            "reverify_after_days": None,
        },
    )
    ready, reasons = await production_policy_readiness(
        db_session,
        settings=Settings(
            environment="production",
            age_assurance_provider="verifymyage",
            verifymyage_environment="production",
            verifymyage_client_id="configured-client",
            verifymyage_client_secret="configured-secret",
            api_origin="https://api.example.test",
            web_origin="https://example.test",
            cookie_secure=True,
            compliance_fallback_country="PT",
        ),
    )
    assert not ready
    assert "ACTIVE_POLICY_REVERIFY_REQUIRED" in reasons


@pytest.mark.asyncio
async def test_production_readiness_rejects_verifymyage_rules_the_adapter_cannot_prove(
    db_session,
):
    await _publish_policy(
        db_session,
        rule_changes={
            "age_provider": "verifymyage",
            "fan_age_verification_required": True,
            "required_assurance_level": AgeAssuranceLevel.medium,
            "minimum_age": 21,
            "reverify_after_days": 30,
        },
    )
    ready, reasons = await production_policy_readiness(
        db_session,
        settings=Settings(
            environment="production",
            age_assurance_provider="verifymyage",
            verifymyage_environment="production",
            verifymyage_client_id="configured-client",
            verifymyage_client_secret="configured-secret",
            api_origin="https://api.example.test",
            web_origin="https://example.test",
            cookie_secure=True,
            compliance_fallback_country="PT",
        ),
    )
    assert not ready
    assert "ACTIVE_POLICY_PROVIDER_ASSURANCE_UNSUPPORTED" in reasons
    assert "ACTIVE_POLICY_PROVIDER_MINIMUM_AGE_UNSUPPORTED" in reasons


@pytest.mark.asyncio
async def test_production_readiness_rejects_each_invalid_linked_template_state(db_session):
    now = datetime.now(UTC)
    actor, template = await _actor_and_template(db_session)
    current = await effective_policy_for_country(db_session, "PT", now=now)
    assert current is not None
    settings = Settings(
        environment="production",
        age_assurance_provider="verifymyage",
        verifymyage_environment="production",
        verifymyage_client_id="configured-client",
        verifymyage_client_secret="configured-secret",
        api_origin="https://api.example.test",
        web_origin="https://example.test",
        cookie_secure=True,
        compliance_fallback_country="PT",
    )

    draft = await create_template_revision(
        db_session,
        template_id=template.id,
        rules=current.rules,
        status=CompliancePolicyStatus.draft,
        effective_from=now - timedelta(seconds=1),
        effective_until=None,
        actor_user_id=actor.id,
        reviewed_at=now,
        reviewed_by_user_id=actor.id,
        change_reason="Draft template must not become effective",
    )
    await create_jurisdiction_revision(
        db_session,
        country_code="PT",
        template_revision_id=draft.id,
        overrides=PolicyOverrides(),
        status=CompliancePolicyStatus.active,
        effective_from=now - timedelta(seconds=1),
        effective_until=None,
        actor_user_id=actor.id,
        reviewed_at=now,
        reviewed_by_user_id=actor.id,
        change_reason="Reference draft for readiness regression",
    )
    _, draft_reasons = await production_policy_readiness(db_session, settings=settings, now=now)
    assert "ACTIVE_POLICY_TEMPLATE_NOT_PUBLISHED" in draft_reasons

    unreviewed = CompliancePolicyTemplateRevision(
        template_id=template.id,
        version=draft.version + 1,
        status=CompliancePolicyStatus.active,
        rules_json=current.rules.model_dump(mode="json"),
        is_demo=False,
        effective_from=now - timedelta(seconds=1),
        created_by_user_id=actor.id,
        reviewed_at=None,
        reviewed_by_user_id=None,
        change_reason="Unreviewed template readiness regression",
    )
    db_session.add(unreviewed)
    await db_session.flush()
    await create_jurisdiction_revision(
        db_session,
        country_code="PT",
        template_revision_id=unreviewed.id,
        overrides=PolicyOverrides(),
        status=CompliancePolicyStatus.active,
        effective_from=now - timedelta(seconds=1),
        effective_until=None,
        actor_user_id=actor.id,
        reviewed_at=now,
        reviewed_by_user_id=actor.id,
        change_reason="Reference unreviewed template for readiness regression",
    )
    _, review_reasons = await production_policy_readiness(db_session, settings=settings, now=now)
    assert "ACTIVE_POLICY_TEMPLATE_UNREVIEWED" in review_reasons

    expired = await create_template_revision(
        db_session,
        template_id=template.id,
        rules=current.rules,
        status=CompliancePolicyStatus.active,
        effective_from=now - timedelta(days=2),
        effective_until=now - timedelta(days=1),
        actor_user_id=actor.id,
        reviewed_at=now - timedelta(days=2),
        reviewed_by_user_id=actor.id,
        change_reason="Expired template readiness regression",
    )
    await create_jurisdiction_revision(
        db_session,
        country_code="PT",
        template_revision_id=expired.id,
        overrides=PolicyOverrides(),
        status=CompliancePolicyStatus.active,
        effective_from=now - timedelta(seconds=1),
        effective_until=None,
        actor_user_id=actor.id,
        reviewed_at=now,
        reviewed_by_user_id=actor.id,
        change_reason="Reference expired template for readiness regression",
    )
    _, expiry_reasons = await production_policy_readiness(db_session, settings=settings, now=now)
    assert "ACTIVE_POLICY_TEMPLATE_NOT_EFFECTIVE" in expiry_reasons

    demo = await create_template_revision(
        db_session,
        template_id=template.id,
        rules=current.rules,
        status=CompliancePolicyStatus.active,
        effective_from=now - timedelta(seconds=1),
        effective_until=None,
        actor_user_id=actor.id,
        reviewed_at=now,
        reviewed_by_user_id=actor.id,
        change_reason="Demo template readiness regression",
        is_demo=True,
    )
    await create_jurisdiction_revision(
        db_session,
        country_code="PT",
        template_revision_id=demo.id,
        overrides=PolicyOverrides(),
        status=CompliancePolicyStatus.active,
        effective_from=now - timedelta(seconds=1),
        effective_until=None,
        actor_user_id=actor.id,
        reviewed_at=now,
        reviewed_by_user_id=actor.id,
        change_reason="Reference demo template for readiness regression",
    )
    _, demo_reasons = await production_policy_readiness(db_session, settings=settings, now=now)
    assert "ACTIVE_POLICY_TEMPLATE_DEMO" in demo_reasons


@pytest.mark.asyncio
async def test_manual_review_is_bounded_audited_and_notified_without_sensitive_payload(db_session):
    now = datetime.now(UTC)
    actor, _ = await _actor_and_template(db_session)
    user = await _user(db_session, "manual-review@example.test", country_code="PT")
    record = await _age_record(
        db_session,
        user=user,
        status=AgeVerificationStatus.review_required,
        assurance=AgeAssuranceLevel.none,
        threshold=None,
        expires_at=None,
    )
    with pytest.raises(AgeVerificationError) as caught:
        await review_verification(
            db_session,
            verification_id=record.id,
            actor_user_id=actor.id,
            status=AgeVerificationStatus.verified,
            change_reason="Approve normalized result",
            achieved_assurance_level=AgeAssuranceLevel.self_attested,
            achieved_minimum_age=18,
            expires_at=now + timedelta(days=91),
            now=now,
        )
    assert caught.value.code == "REVIEW_EXPIRY_OUT_OF_BOUNDS"

    reviewed = await review_verification(
        db_session,
        verification_id=record.id,
        actor_user_id=actor.id,
        status=AgeVerificationStatus.verified,
        change_reason="Approve normalized result",
        achieved_assurance_level=AgeAssuranceLevel.self_attested,
        achieved_minimum_age=18,
        expires_at=now + timedelta(days=30),
        now=now,
    )
    assert reviewed.status is AgeVerificationStatus.verified
    intent = await db_session.scalar(
        select(NotificationIntent).where(
            NotificationIntent.notification_type == "AGE_VERIFICATION_COMPLETED",
            NotificationIntent.recipient_user_id == user.id,
        )
    )
    assert intent is not None

    _, changed_revision, _, changed_rules = await _publish_policy(
        db_session,
        rule_changes={"creator_identity_required": True},
    )
    assert changed_rules.creator_identity_required
    audit = await db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "compliance.template_revision_created")
        .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
        .limit(1)
    )
    assert audit is not None
    changed_fields = {change["field"] for change in audit.metadata_json["changes"]}
    assert "creator_identity_required" in changed_fields
    assert audit.metadata_json["effective_from"] == changed_revision.effective_from.isoformat()
    assert audit.metadata_json["previous_effective_from"] is not None
    assert "effective_until" in audit.metadata_json
    assert "previous_effective_until" in audit.metadata_json


@pytest.mark.asyncio
async def test_admin_revocation_consumes_state_and_delayed_callback_cannot_restore_access(
    db_session,
):
    actor, _ = await _actor_and_template(db_session)
    user = await _user(db_session, "review-callback-race@example.test", country_code="PT")
    started = await start_age_verification(db_session, user=user, country_code="PT")
    state = _state_from_authorization_url(started.authorization_url)

    reviewed = await review_verification(
        db_session,
        verification_id=started.record.id,
        actor_user_id=actor.id,
        status=AgeVerificationStatus.revoked,
        change_reason="Revoke pending evidence after controlled review",
    )
    assert reviewed.status is AgeVerificationStatus.revoked
    assert reviewed.revoked_at is not None
    assert reviewed.state_consumed_at is not None

    with pytest.raises(AgeVerificationError) as caught:
        await complete_browser_callback(
            db_session,
            provider_name="test",
            state=state,
            code="approved",
        )
    assert caught.value.code == "STATE_REPLAYED"
    assert reviewed.status is AgeVerificationStatus.revoked
    assert reviewed.revoked_at is not None

    decision = await resolve_compliance_decision(
        db_session,
        user=user,
        feature=ComplianceFeature.adult_media,
        adult_restricted=True,
    )
    assert not decision.allowed
    assert decision.code == "AGE_VERIFICATION_REVOKED"


@pytest.mark.asyncio
async def test_revocation_is_immutable_and_idempotent_terminalization_preserves_evidence(
    db_session,
):
    now = datetime.now(UTC)
    actor, _ = await _actor_and_template(db_session)
    user = await _user(db_session, "immutable-revocation@example.test", country_code="PT")
    started = await start_age_verification(db_session, user=user, country_code="PT", now=now)
    state = _state_from_authorization_url(started.authorization_url)
    revoked = await review_verification(
        db_session,
        verification_id=started.record.id,
        actor_user_id=actor.id,
        status=AgeVerificationStatus.revoked,
        change_reason="Create an immutable revocation tombstone",
        now=now,
    )
    original_revoked_at = revoked.revoked_at
    original_reason = revoked.failure_reason_code
    original_state_consumed_at = revoked.state_consumed_at

    with pytest.raises(AgeVerificationError) as caught:
        await review_verification(
            db_session,
            verification_id=revoked.id,
            actor_user_id=actor.id,
            status=AgeVerificationStatus.verified,
            change_reason="Attempt an in-place re-approval",
            achieved_assurance_level=AgeAssuranceLevel.self_attested,
            achieved_minimum_age=18,
            expires_at=now + timedelta(days=30),
            now=now + timedelta(days=1),
        )
    assert caught.value.code == "REVOCATION_IMMUTABLE"
    assert revoked.status is AgeVerificationStatus.revoked
    assert revoked.revoked_at == original_revoked_at
    assert revoked.failure_reason_code == original_reason
    assert revoked.state_consumed_at == original_state_consumed_at

    repeated = await review_verification(
        db_session,
        verification_id=revoked.id,
        actor_user_id=actor.id,
        status=AgeVerificationStatus.revoked,
        change_reason="Confirm the existing revocation without rewriting it",
        now=now + timedelta(days=2),
    )
    assert repeated.revoked_at == original_revoked_at
    assert repeated.failure_reason_code == original_reason

    # Simulate a retained legacy tombstone whose callback state predates the
    # terminalization invariant. An idempotent lifecycle revoke repairs only
    # the missing one-time state evidence and preserves the original facts.
    repeated.state_consumed_at = None
    await db_session.flush()
    terminalized = await revoke_verification(
        db_session,
        verification_id=repeated.id,
        actor_user_id=actor.id,
        reason_code="IDEMPOTENT_TERMINALIZATION",
        now=now + timedelta(days=3),
    )
    assert terminalized.revoked_at == original_revoked_at
    assert terminalized.failure_reason_code == original_reason
    assert terminalized.state_consumed_at == now + timedelta(days=3)

    with pytest.raises(AgeVerificationError) as replay:
        await complete_browser_callback(
            db_session,
            provider_name="test",
            state=state,
            code="approved",
            now=now + timedelta(days=3),
        )
    assert replay.value.code == "STATE_REPLAYED"


@pytest.mark.asyncio
async def test_lifecycle_revocation_consumes_state_before_delayed_callback(db_session):
    actor, _ = await _actor_and_template(db_session)
    user = await _user(db_session, "lifecycle-callback-race@example.test", country_code="PT")
    started = await start_age_verification(db_session, user=user, country_code="PT")
    state = _state_from_authorization_url(started.authorization_url)

    revoked = await revoke_verification(
        db_session,
        verification_id=started.record.id,
        actor_user_id=actor.id,
        reason_code="CONTROLLED_LIFECYCLE_REVOCATION",
    )
    assert revoked.status is AgeVerificationStatus.revoked
    assert revoked.state_consumed_at is not None

    with pytest.raises(AgeVerificationError) as caught:
        await complete_browser_callback(
            db_session,
            provider_name="test",
            state=state,
            code="approved",
        )
    assert caught.value.code == "STATE_REPLAYED"
    assert revoked.status is AgeVerificationStatus.revoked
