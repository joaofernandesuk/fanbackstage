from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select

from app.api.deps import Db, OperatorRecoveryIdentity
from app.audit.service import record_event
from app.compliance.age_verification import (
    AgeVerificationError,
    probe_provider,
    review_verification,
)
from app.compliance.http import compose_legal_acceptance_decision
from app.compliance.policy import (
    CompliancePolicyError,
    create_feature_flag_revision,
    create_jurisdiction_revision,
    create_policy_template,
    create_template_revision,
    effective_policy_for_country,
    register_country,
    resolve_compliance_decision,
    set_account_country,
    set_country_enabled,
)
from app.compliance.types import JurisdictionSignals
from app.core.config import get_settings
from app.creators import service as creator_service
from app.integrations.age_verification import ProviderError, get_age_verification_provider
from app.models.audit import AuditEvent
from app.models.compliance import (
    AgeProviderProbe,
    AgeVerificationRecord,
    AgeVerificationStatus,
    CompliancePolicyStatus,
    CompliancePolicyTemplate,
    CompliancePolicyTemplateRevision,
    CountryRegistry,
    FeatureFlagRevision,
    JurisdictionPolicyRevision,
    ProviderProbeStatus,
)
from app.models.creator import CreatorProfile, CreatorVerification, VerificationStatus
from app.models.identity import User
from app.permissions.policies import Permission, authorize
from app.schemas.compliance import (
    AccountCountryReviewInput,
    ComplianceSimulationInput,
    CountryAvailabilityInput,
    CountryRegistryInput,
    FeatureFlagRevisionInput,
    JurisdictionRevisionInput,
    PolicyTemplateInput,
    PolicyTemplateResponse,
    ProviderProbeInput,
    TemplateRevisionInput,
    VerificationReviewInput,
)
from app.schemas.trust_safety import CreatorKycDecisionInput

router = APIRouter(prefix="/admin/compliance", tags=["admin-compliance"])


async def _enqueue_live_authority_reconciliation(db: Db) -> None:
    """Queue newly-invalid live controls in the policy transaction.

    The worker is the only LiveKit caller; this only records ending/eviction
    intents so a committed policy cannot leave known-invalid live access
    without durable recovery work.
    """

    from app.streaming.service import reconcile_live_compliance_authority

    await reconcile_live_compliance_authority(db, commit_each=False)


def _page(page: int, page_size: int) -> tuple[int, int]:
    return (page - 1) * page_size, page_size


@router.get("/creator-kyc")
async def creator_kyc_operations(
    identity: OperatorRecoveryIdentity,
    db: Db,
    search: str | None = None,
    provider: str | None = None,
    country_code: str | None = None,
    status: VerificationStatus | None = None,
    failure_reason: str | None = None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> dict:
    """Safe operational projection; provider evidence and secrets stay private."""
    authorize(identity[0], Permission.COMPLIANCE_VERIFICATION_VIEW)
    filters = []
    if search:
        pattern = f"%{search.strip()}%"
        filters.append(
            or_(
                User.email.ilike(pattern),
                CreatorProfile.username.ilike(pattern),
                CreatorProfile.display_name.ilike(pattern),
            )
        )
    if provider:
        filters.append(CreatorVerification.provider == provider)
    if country_code:
        filters.append(CreatorVerification.country_code == country_code.upper())
    if status:
        filters.append(CreatorVerification.status == status)
    if failure_reason:
        filters.append(CreatorVerification.failure_reason_code == failure_reason)
    if starts_at:
        filters.append(CreatorVerification.created_at >= starts_at)
    if ends_at:
        filters.append(CreatorVerification.created_at <= ends_at)
    offset, limit = _page(page, page_size)
    base = (
        select(CreatorVerification, CreatorProfile, User)
        .join(CreatorProfile, CreatorProfile.id == CreatorVerification.creator_profile_id)
        .join(User, User.id == CreatorProfile.user_id)
    )
    total = await db.scalar(
        select(func.count())
        .select_from(CreatorVerification)
        .join(CreatorProfile, CreatorProfile.id == CreatorVerification.creator_profile_id)
        .join(User, User.id == CreatorProfile.user_id)
        .where(*filters)
    )
    rows = (
        await db.execute(
            base.where(*filters)
            .order_by(CreatorVerification.created_at.desc(), CreatorVerification.id.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return {
        "items": [
            {
                "verification_id": str(verification.id),
                "creator": {
                    "display_name": profile.display_name,
                    "username": profile.username,
                    "email": user.email,
                    "application_status": profile.status.value,
                },
                "provider": verification.provider,
                "provider_reference": verification.provider_reference,
                "status": verification.status.value,
                "country_code": verification.country_code,
                "started_at": verification.created_at,
                "verified_at": verification.verified_at,
                "expires_at": verification.expires_at,
                "failure_reason_code": verification.failure_reason_code,
                "review_category": verification.metadata_json.get("review_category")
                or verification.failure_reason_code,
            }
            for verification, profile, user in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": int(total or 0),
    }


@router.get("/creator-kyc/{verification_id}")
async def creator_kyc_detail(
    verification_id: UUID, identity: OperatorRecoveryIdentity, db: Db
) -> dict:
    authorize(identity[0], Permission.COMPLIANCE_VERIFICATION_VIEW)
    row = await db.execute(
        select(CreatorVerification, CreatorProfile, User)
        .join(CreatorProfile, CreatorProfile.id == CreatorVerification.creator_profile_id)
        .join(User, User.id == CreatorProfile.user_id)
        .where(CreatorVerification.id == verification_id)
    )
    record = row.one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Creator KYC review not found")
    verification, profile, user = record
    audits = list(
        await db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.target_type == "creator_verification",
                AuditEvent.target_id == str(verification.id),
            )
            .order_by(AuditEvent.created_at.desc())
            .limit(100)
        )
    )
    return {
        "verification_id": str(verification.id),
        "creator": {
            "display_name": profile.display_name,
            "username": profile.username,
            "email": user.email,
            "application_status": profile.status.value,
        },
        "provider": verification.provider,
        "provider_reference": verification.provider_reference,
        "country_code": verification.country_code,
        "status": verification.status.value,
        "started_at": verification.created_at,
        "verified_at": verification.verified_at,
        "expires_at": verification.expires_at,
        "review_category": verification.metadata_json.get("review_category")
        or verification.failure_reason_code,
        "identity_verified": verification.identity_verified,
        "adult_verified": verification.adult_verified,
        "allowed_actions": ["reject", "request_reverification", "leave_in_review"]
        if verification.status is VerificationStatus.needs_review
        else [],
        "manual_approval_permitted": False,
        "audit": [
            {
                "type": event.event_type,
                "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
                "created_at": event.created_at,
            }
            for event in audits
        ],
    }


@router.post("/creator-kyc/{verification_id}/decision")
async def decide_creator_kyc(
    verification_id: UUID,
    payload: CreatorKycDecisionInput,
    identity: OperatorRecoveryIdentity,
    db: Db,
) -> dict:
    authorize(identity[0], Permission.COMPLIANCE_VERIFICATION_REVIEW)
    try:
        verification = await creator_service.review_creator_kyc(
            db,
            verification_id=verification_id,
            reviewer=identity[0],
            action=payload.action,
            reason=payload.reason,
            expected_status=VerificationStatus(payload.expected_status),
        )
        await db.commit()
        return {"verification_id": str(verification.id), "status": verification.status.value}
    except ValueError as exc:
        await db.rollback()
        status_code = (
            404
            if "not found" in str(exc).lower()
            else 409
            if "changed" in str(exc).lower()
            else 400
        )
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


def _raise_bad_request(exc: Exception) -> None:
    code = getattr(exc, "code", None)
    detail = {"code": code, "message": str(exc)} if code else str(exc)
    raise HTTPException(status_code=400, detail=detail) from exc


@router.put("/users/{user_id}/country")
async def review_account_country(
    user_id: UUID,
    payload: AccountCountryReviewInput,
    identity: OperatorRecoveryIdentity,
    db: Db,
) -> dict[str, str]:
    """Resolve legacy/mobility cases when trusted GeoIP self-service is unavailable."""

    authorize(identity[0], Permission.COMPLIANCE_JURISDICTION_MANAGE)
    try:
        user = await set_account_country(
            db,
            user_id=user_id,
            country_code=payload.country_code,
            actor_user_id=identity[0].id,
            change_reason=payload.change_reason,
            source="operator_review",
        )
        from app.streaming.service import evict_user_from_active_live

        await evict_user_from_active_live(
            db,
            user.id,
            reason="account_country_operator_review",
            force=True,
        )
        await db.commit()
    except CompliancePolicyError as exc:
        await db.rollback()
        _raise_bad_request(exc)
    return {"user_id": str(user.id), "country_code": user.country_code or payload.country_code}


@router.get("/templates")
async def list_templates(
    identity: OperatorRecoveryIdentity,
    db: Db,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> dict:
    authorize(identity[0], Permission.COMPLIANCE_VIEW)
    filters = []
    if search:
        pattern = f"%{search.strip()}%"
        filters.append(
            or_(
                CompliancePolicyTemplate.key.ilike(pattern),
                CompliancePolicyTemplate.name.ilike(pattern),
            )
        )
    total = await db.scalar(
        select(func.count()).select_from(CompliancePolicyTemplate).where(*filters)
    )
    offset, limit = _page(page, page_size)
    rows = (
        await db.scalars(
            select(CompliancePolicyTemplate)
            .where(*filters)
            .order_by(CompliancePolicyTemplate.key)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return {
        "items": [PolicyTemplateResponse.model_validate(row).model_dump() for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total or 0,
    }


@router.post("/templates", response_model=PolicyTemplateResponse)
async def add_template(
    payload: PolicyTemplateInput, identity: OperatorRecoveryIdentity, db: Db
) -> PolicyTemplateResponse:
    authorize(identity[0], Permission.COMPLIANCE_POLICY_MANAGE)
    try:
        row = await create_policy_template(
            db,
            key=payload.key,
            name=payload.name,
            description=payload.description,
            actor_user_id=identity[0].id,
            change_reason=payload.change_reason,
        )
        await db.commit()
        return PolicyTemplateResponse.model_validate(row)
    except CompliancePolicyError as exc:
        await db.rollback()
        _raise_bad_request(exc)


@router.post("/templates/{template_id}/revisions")
async def add_template_revision(
    template_id: UUID,
    payload: TemplateRevisionInput,
    identity: OperatorRecoveryIdentity,
    db: Db,
) -> dict:
    authorize(identity[0], Permission.COMPLIANCE_POLICY_MANAGE)
    reviewed_at = datetime.now(UTC) if payload.reviewed else None
    try:
        row = await create_template_revision(
            db,
            template_id=template_id,
            rules=payload.rules,
            status=payload.status,
            effective_from=payload.effective_from,
            effective_until=payload.effective_until,
            actor_user_id=identity[0].id,
            reviewed_at=reviewed_at,
            reviewed_by_user_id=identity[0].id if payload.reviewed else None,
            change_reason=payload.change_reason,
            is_demo=payload.is_demo,
        )
        await _enqueue_live_authority_reconciliation(db)
        await db.commit()
        return {"id": row.id, "version": row.version, "status": row.status.value}
    except CompliancePolicyError as exc:
        await db.rollback()
        _raise_bad_request(exc)


@router.get("/templates/{template_id}/revisions")
async def list_template_revisions(
    template_id: UUID, identity: OperatorRecoveryIdentity, db: Db
) -> list[dict]:
    authorize(identity[0], Permission.COMPLIANCE_VIEW)
    rows = (
        await db.scalars(
            select(CompliancePolicyTemplateRevision)
            .where(CompliancePolicyTemplateRevision.template_id == template_id)
            .order_by(CompliancePolicyTemplateRevision.version.desc())
        )
    ).all()
    return [
        {
            "id": row.id,
            "version": row.version,
            "status": row.status.value,
            "rules": row.rules_json,
            "is_demo": row.is_demo,
            "effective_from": row.effective_from,
            "effective_until": row.effective_until,
            "reviewed_at": row.reviewed_at,
            "change_reason": row.change_reason,
        }
        for row in rows
    ]


@router.get("/jurisdictions")
async def list_jurisdictions(
    identity: OperatorRecoveryIdentity,
    db: Db,
    country_code: str | None = None,
    status: CompliancePolicyStatus | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> dict:
    authorize(identity[0], Permission.COMPLIANCE_VIEW)
    filters = []
    if country_code:
        filters.append(JurisdictionPolicyRevision.country_code == country_code.upper())
    if status:
        filters.append(JurisdictionPolicyRevision.status == status)
    total = await db.scalar(
        select(func.count()).select_from(JurisdictionPolicyRevision).where(*filters)
    )
    offset, limit = _page(page, page_size)
    rows = (
        await db.scalars(
            select(JurisdictionPolicyRevision)
            .where(*filters)
            .order_by(
                JurisdictionPolicyRevision.country_code,
                JurisdictionPolicyRevision.version.desc(),
            )
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "country_code": row.country_code,
                "version": row.version,
                "template_revision_id": row.template_revision_id,
                "status": row.status.value,
                "overrides": row.overrides_json,
                "is_demo": row.is_demo,
                "effective_from": row.effective_from,
                "effective_until": row.effective_until,
                "reviewed_at": row.reviewed_at,
                "change_reason": row.change_reason,
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total or 0,
    }


@router.post("/jurisdictions/{country_code}/revisions")
async def add_jurisdiction_revision(
    country_code: str,
    payload: JurisdictionRevisionInput,
    identity: OperatorRecoveryIdentity,
    db: Db,
) -> dict:
    authorize(identity[0], Permission.COMPLIANCE_JURISDICTION_MANAGE)
    reviewed_at = datetime.now(UTC) if payload.reviewed else None
    try:
        row = await create_jurisdiction_revision(
            db,
            country_code=country_code,
            template_revision_id=payload.template_revision_id,
            overrides=payload.overrides,
            status=payload.status,
            effective_from=payload.effective_from,
            effective_until=payload.effective_until,
            actor_user_id=identity[0].id,
            reviewed_at=reviewed_at,
            reviewed_by_user_id=identity[0].id if payload.reviewed else None,
            change_reason=payload.change_reason,
            is_demo=payload.is_demo,
        )
        await _enqueue_live_authority_reconciliation(db)
        await db.commit()
        return {"id": row.id, "version": row.version, "status": row.status.value}
    except CompliancePolicyError as exc:
        await db.rollback()
        _raise_bad_request(exc)


@router.post("/feature-flags")
async def add_feature_flag_revision(
    payload: FeatureFlagRevisionInput, identity: OperatorRecoveryIdentity, db: Db
) -> dict:
    authorize(identity[0], Permission.FEATURE_FLAG_MANAGE)
    try:
        row = await create_feature_flag_revision(
            db,
            feature=payload.feature,
            country_scope=payload.country_scope,
            enabled=payload.enabled,
            effective_from=payload.effective_from,
            effective_until=payload.effective_until,
            actor_user_id=identity[0].id,
            change_reason=payload.change_reason,
            is_demo=payload.is_demo,
        )
        await _enqueue_live_authority_reconciliation(db)
        await db.commit()
        return {"id": row.id, "version": row.version}
    except CompliancePolicyError as exc:
        await db.rollback()
        _raise_bad_request(exc)


@router.get("/feature-flags")
async def list_feature_flag_revisions(
    identity: OperatorRecoveryIdentity,
    db: Db,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> dict:
    authorize(identity[0], Permission.COMPLIANCE_VIEW)
    total = await db.scalar(select(func.count()).select_from(FeatureFlagRevision))
    offset, limit = _page(page, page_size)
    rows = (
        await db.scalars(
            select(FeatureFlagRevision)
            .order_by(FeatureFlagRevision.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "feature": row.feature.value,
                "country_scope": row.country_scope or None,
                "version": row.version,
                "enabled": row.enabled,
                "is_demo": row.is_demo,
                "effective_from": row.effective_from,
                "effective_until": row.effective_until,
                "change_reason": row.change_reason,
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total or 0,
    }


@router.post("/countries")
async def add_country(
    payload: CountryRegistryInput, identity: OperatorRecoveryIdentity, db: Db
) -> dict:
    authorize(identity[0], Permission.COMPLIANCE_JURISDICTION_MANAGE)
    try:
        row = await register_country(
            db,
            code=payload.code,
            name=payload.name,
            actor_user_id=identity[0].id,
            change_reason=payload.change_reason,
        )
        await _enqueue_live_authority_reconciliation(db)
        await db.commit()
        return {"code": row.code, "name": row.name, "enabled": row.enabled}
    except CompliancePolicyError as exc:
        await db.rollback()
        _raise_bad_request(exc)


@router.get("/countries")
async def admin_countries(
    identity: OperatorRecoveryIdentity,
    db: Db,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> dict:
    authorize(identity[0], Permission.COMPLIANCE_VIEW)
    filters = []
    if search:
        pattern = f"%{search.strip()}%"
        filters.append(
            or_(CountryRegistry.code.ilike(pattern), CountryRegistry.name.ilike(pattern))
        )
    total = await db.scalar(select(func.count()).select_from(CountryRegistry).where(*filters))
    offset, limit = _page(page, page_size)
    countries = (
        await db.scalars(
            select(CountryRegistry)
            .where(*filters)
            .order_by(CountryRegistry.name, CountryRegistry.code)
            .offset(offset)
            .limit(limit)
        )
    ).all()
    items = []
    for country in countries:
        effective = await effective_policy_for_country(db, country.code)
        rules = effective.rules if effective else None
        items.append(
            {
                "code": country.code,
                "name": country.name,
                "enabled": country.enabled,
                "effective_policy": (
                    {
                        "id": effective.jurisdiction_revision.id,
                        "version": effective.jurisdiction_revision.version,
                        "status": effective.jurisdiction_revision.status.value,
                        "template_revision_id": effective.template_revision.id,
                        "template_version": effective.template_revision.version,
                        "effective_from": effective.jurisdiction_revision.effective_from,
                        "effective_until": effective.jurisdiction_revision.effective_until,
                        "minimum_age": rules.minimum_age,
                        "fan_age_verification_required": rules.fan_age_verification_required,
                        "required_assurance_level": rules.required_assurance_level.value,
                        "creator_identity_required": rules.creator_identity_required,
                        "creator_age_verification_required": rules.creator_age_verification_required,
                        "payout_kyc_required": rules.payout_kyc_required,
                        "co_performer_verification_required": (
                            rules.co_performer_verification_required
                        ),
                        "release_required": rules.release_required,
                        "age_provider": rules.age_provider,
                    }
                    if effective and rules
                    else None
                ),
            }
        )
    return {"items": items, "page": page, "page_size": page_size, "total": total or 0}


@router.put("/countries/{country_code}/availability")
async def change_country_availability(
    country_code: str,
    payload: CountryAvailabilityInput,
    identity: OperatorRecoveryIdentity,
    db: Db,
) -> dict:
    authorize(identity[0], Permission.COMPLIANCE_JURISDICTION_MANAGE)
    try:
        row = await set_country_enabled(
            db,
            code=country_code,
            enabled=payload.enabled,
            actor_user_id=identity[0].id,
            change_reason=payload.change_reason,
        )
        await _enqueue_live_authority_reconciliation(db)
        await db.commit()
        return {"code": row.code, "enabled": row.enabled}
    except CompliancePolicyError as exc:
        await db.rollback()
        _raise_bad_request(exc)


@router.get("/verifications")
async def search_verifications(
    identity: OperatorRecoveryIdentity,
    db: Db,
    search: str | None = None,
    country_code: str | None = None,
    provider: str | None = None,
    status: AgeVerificationStatus | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> dict:
    authorize(identity[0], Permission.COMPLIANCE_VERIFICATION_VIEW)
    filters = []
    query = select(AgeVerificationRecord)
    count_query = select(func.count()).select_from(AgeVerificationRecord)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.outerjoin(User, User.id == AgeVerificationRecord.user_id)
        count_query = count_query.outerjoin(User, User.id == AgeVerificationRecord.user_id)
        filters.append(User.email.ilike(pattern))
    if country_code:
        filters.append(AgeVerificationRecord.country_code == country_code.upper())
    if provider:
        filters.append(AgeVerificationRecord.provider == provider)
    if status:
        filters.append(AgeVerificationRecord.status == status)
    total = await db.scalar(count_query.where(*filters))
    offset, limit = _page(page, page_size)
    rows = (
        await db.scalars(
            query.where(*filters)
            .order_by(AgeVerificationRecord.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "user_id": row.user_id,
                "anonymous_session_id": row.anonymous_session_id,
                "anonymous": row.user_id is None,
                "provider": row.provider,
                "country_code": row.country_code,
                "applicable_policy_id": row.applicable_policy_id,
                "applicable_policy_version": row.applicable_policy_version,
                "status": row.status.value,
                "required_minimum_age": row.required_minimum_age,
                "required_assurance": row.required_assurance_level.value,
                "achieved_minimum_age": row.achieved_minimum_age,
                "achieved_assurance": row.achieved_assurance_level.value,
                "initiated_at": row.initiated_at,
                "verified_at": row.verified_at,
                "failed_at": row.failed_at,
                "expires_at": row.expires_at,
                "revoked_at": row.revoked_at,
                "failure_reason_code": row.failure_reason_code,
                "retryable": row.retryable,
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total or 0,
    }


@router.post("/verifications/{verification_id}/review")
async def review_age_verification(
    verification_id: UUID,
    payload: VerificationReviewInput,
    identity: OperatorRecoveryIdentity,
    db: Db,
) -> dict:
    authorize(identity[0], Permission.COMPLIANCE_VERIFICATION_REVIEW)
    try:
        row = await review_verification(
            db,
            verification_id=verification_id,
            actor_user_id=identity[0].id,
            status=AgeVerificationStatus(payload.status),
            change_reason=payload.change_reason,
            achieved_assurance_level=payload.achieved_assurance_level,
            achieved_minimum_age=payload.achieved_minimum_age,
            expires_at=payload.expires_at,
        )
        await db.commit()
        return {"id": row.id, "status": row.status.value}
    except AgeVerificationError as exc:
        await db.rollback()
        _raise_bad_request(exc)


@router.post("/providers/probe")
async def run_provider_probe(
    payload: ProviderProbeInput, identity: OperatorRecoveryIdentity, db: Db
) -> dict:
    authorize(identity[0], Permission.COMPLIANCE_PROVIDER_MANAGE)
    try:
        diagnostic, row = await probe_provider(db, provider_name=payload.provider)
        await db.flush()
        await record_event(
            db,
            "compliance.provider_probe_completed",
            actor_user_id=identity[0].id,
            target_type="age_provider_probe",
            target_id=str(row.id),
            metadata={
                "provider": row.provider,
                "environment": row.environment,
                "status": row.status.value,
                "configuration_complete": row.configuration_complete,
                "allowed_redirect": diagnostic.allowed_redirect,
                "error_code": row.error_code,
            },
        )
        await db.commit()
        return {
            "id": row.id,
            "provider": row.provider,
            "environment": row.environment,
            "status": row.status.value,
            "configuration_complete": diagnostic.configuration_complete,
            "callback_url": diagnostic.callback_url,
            "allowed_redirect": diagnostic.allowed_redirect,
            "error_code": diagnostic.error_code,
            "capabilities": diagnostic.capabilities.public_dict(),
        }
    except AgeVerificationError as exc:
        await db.rollback()
        _raise_bad_request(exc)


@router.get("/providers")
async def provider_inventory(identity: OperatorRecoveryIdentity, db: Db) -> list[dict]:
    authorize(identity[0], Permission.COMPLIANCE_VIEW)
    settings = get_settings()
    names = ("verifymyage", "test", "development_self_attestation")
    inventory = []
    for name in names:
        latest = await db.scalar(
            select(AgeProviderProbe)
            .where(AgeProviderProbe.provider == name)
            .order_by(AgeProviderProbe.probed_at.desc())
            .limit(1)
        )
        last_healthy = await db.scalar(
            select(AgeProviderProbe.probed_at)
            .where(
                AgeProviderProbe.provider == name,
                AgeProviderProbe.status.in_(
                    [ProviderProbeStatus.healthy, ProviderProbeStatus.degraded]
                ),
            )
            .order_by(AgeProviderProbe.probed_at.desc())
            .limit(1)
        )
        capabilities = None
        configuration_complete = False
        environment = None
        try:
            provider = get_age_verification_provider(name, settings=settings)
            environment = provider.environment
            capabilities = provider.get_capabilities().public_dict()
            configuration_complete = (
                bool(settings.verifymyage_client_id and settings.verifymyage_client_secret)
                if name == "verifymyage"
                else settings.environment == "test" or settings.age_test_provider_enabled
            )
        except ProviderError:
            environment = "blocked"
        inventory.append(
            {
                "provider": name,
                "selected": settings.age_assurance_provider == name,
                "enabled": (settings.age_assurance_provider == name and configuration_complete),
                "environment": environment,
                "configuration_complete": configuration_complete,
                "capabilities": capabilities,
                "latest_probe": (
                    {
                        "status": latest.status.value,
                        "error_code": latest.error_code,
                        "probed_at": latest.probed_at,
                    }
                    if latest
                    else None
                ),
                "last_healthy_at": last_healthy,
            }
        )
    return inventory


@router.get("/providers/probes")
async def provider_probes(identity: OperatorRecoveryIdentity, db: Db) -> list[dict]:
    authorize(identity[0], Permission.COMPLIANCE_VIEW)
    rows = (
        await db.scalars(
            select(AgeProviderProbe).order_by(AgeProviderProbe.probed_at.desc()).limit(100)
        )
    ).all()
    return [
        {
            "id": row.id,
            "provider": row.provider,
            "environment": row.environment,
            "status": row.status.value,
            "configuration_complete": row.configuration_complete,
            "callback_url": row.callback_url,
            "error_code": row.error_code,
            "probed_at": row.probed_at,
            "capabilities": row.capabilities_json,
        }
        for row in rows
    ]


@router.post("/simulator")
async def simulate_policy(
    payload: ComplianceSimulationInput, identity: OperatorRecoveryIdentity, db: Db
) -> dict:
    authorize(identity[0], Permission.COMPLIANCE_VIEW)
    user = await db.get(User, payload.user_id) if payload.user_id else None
    if payload.user_id and user is None:
        raise HTTPException(status_code=404, detail="Simulation user not found")
    decision = await resolve_compliance_decision(
        db,
        user=user,
        feature=payload.feature,
        signals=JurisdictionSignals(request_country=payload.country_code),
        adult_restricted=payload.adult_restricted,
        legacy_self_attested=False,
    )
    decision, _ = await compose_legal_acceptance_decision(
        db,
        user=user,
        decision=decision,
    )
    return decision.public_dict()


@router.get("/audit")
async def compliance_audit(
    identity: OperatorRecoveryIdentity,
    db: Db,
    search: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
) -> dict:
    authorize(identity[0], Permission.COMPLIANCE_VIEW)
    filters = [
        or_(
            AuditEvent.event_type.like("compliance.%"),
            AuditEvent.event_type.like("legal.%"),
            AuditEvent.event_type.like("site_settings.%"),
            AuditEvent.event_type.like("performer.%"),
            AuditEvent.event_type.like("consent.%"),
            AuditEvent.event_type.like("creator.verification%"),
        )
    ]
    if search:
        filters.append(AuditEvent.event_type.ilike(f"%{search.strip()}%"))
    total = await db.scalar(select(func.count()).select_from(AuditEvent).where(*filters))
    offset, limit = _page(page, page_size)
    rows = (
        await db.scalars(
            select(AuditEvent)
            .where(*filters)
            .order_by(AuditEvent.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return {
        "items": [
            {
                "id": row.id,
                "event_type": row.event_type,
                "actor_user_id": row.actor_user_id,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "correlation_id": row.correlation_id,
                "ip_address": row.ip_address,
                "user_agent": row.user_agent,
                "metadata": row.metadata_json,
                "created_at": row.created_at,
            }
            for row in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": total or 0,
    }
