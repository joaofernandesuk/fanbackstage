from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select

from app.api.deps import CurrentIdentity, Db, OptionalIdentity
from app.compliance.http import resolve_request_compliance_decision
from app.core.config import get_settings
from app.core.rate_limit import enforce_discovery_rate_limit
from app.creators import service
from app.media.projection import safe_public_profile_media_reference
from app.models.compliance import ComplianceFeature
from app.models.creator import (
    CreatorCategory,
    CreatorLanguage,
    CreatorProfile,
    CreatorStatus,
    CreatorVerification,
)
from app.models.social import Follow
from app.schemas.creator import (
    CreatorComplianceEligibilityResponse,
    CreatorProfileUpdate,
    CreatorSelfResponse,
    CreatorUsernameAvailabilityResponse,
    PublicCreatorResponse,
    SocialLinkInput,
    StagingKycOutcomeInput,
    TaxonomyItem,
)
from app.trust_safety import service as trust_safety

router = APIRouter(prefix="/creators", tags=["creators"])


async def request_creator_decision(db: Db, request: Request, user, *, registration: bool):
    return await resolve_request_compliance_decision(
        db,
        request,
        user=user,
        feature=(
            ComplianceFeature.creator_registration
            if registration
            else ComplianceFeature.adult_media
        ),
        adult_restricted=not registration,
    )


async def require_creator_registration(db: Db, request: Request, user) -> None:
    decision = await request_creator_decision(db, request, user, registration=True)
    if not decision.allowed:
        raise HTTPException(
            403,
            {
                "message": decision.reason,
                "code": decision.code,
                "action": decision.action,
                "reason": decision.reason,
            },
        )


def development_verification_enabled() -> bool:
    settings = get_settings()
    return (
        settings.environment in {"development", "test"}
        and settings.kyc_provider == "development"
        and settings.development_kyc_http_enabled
    )


def staging_kyc_sandbox_enabled() -> bool:
    settings = get_settings()
    return (
        settings.environment in {"staging", "test"} and settings.kyc_provider == "staging_sandbox"
    )


async def self_response(db: Db, profile: CreatorProfile) -> CreatorSelfResponse:
    verification = await service.latest_verification(db, profile.id)
    eligibility = await service.resolve_creator_compliance_eligibility(db, profile=profile)
    performer_consent_issue_count = await trust_safety.creator_performer_consent_issue_count(
        db, profile.id
    )
    development_verification_available = (
        profile.status is CreatorStatus.pending_verification and development_verification_enabled()
    )
    staging_kyc_sandbox_available = (
        profile.status is CreatorStatus.pending_verification and staging_kyc_sandbox_enabled()
    )
    available_languages = (
        await db.scalars(
            select(CreatorLanguage)
            .where(CreatorLanguage.enabled.is_(True))
            .order_by(CreatorLanguage.label, CreatorLanguage.code)
        )
    ).all()
    available_categories = (
        await db.scalars(
            select(CreatorCategory)
            .where(CreatorCategory.enabled.is_(True))
            .order_by(CreatorCategory.position, CreatorCategory.slug)
        )
    ).all()
    return CreatorSelfResponse(
        id=profile.id,
        username=profile.username,
        display_name=profile.display_name,
        bio=profile.bio,
        country_code=profile.country_code,
        region=profile.region,
        city=profile.city,
        show_location=profile.show_location,
        timezone=profile.timezone,
        status=profile.status.value,
        is_public=profile.is_public,
        verification_status=verification.status.value if verification else "not_started",
        adult_verified=verification.adult_verified if verification else False,
        creator_compliance=CreatorComplianceEligibilityResponse(
            jurisdiction=eligibility.jurisdiction,
            policy_version=eligibility.policy_version,
            verification_status=eligibility.verification_status,
            verification_expires_at=eligibility.verification_expires_at,
            identity_required=eligibility.identity_required,
            identity_allowed=eligibility.identity_allowed,
            age_required=eligibility.age_required,
            age_allowed=eligibility.age_allowed,
            public_allowed=eligibility.public_allowed,
            payout_kyc_required=eligibility.payout_kyc_required,
            payout_kyc_satisfied=eligibility.payout_kyc_satisfied,
            payout_allowed=eligibility.payout_allowed,
            code=eligibility.code,
            reason=eligibility.reason,
            payout_code=eligibility.payout_code,
        ),
        performer_consent_issue_count=performer_consent_issue_count,
        creator_compliance_action_required=(
            not eligibility.public_allowed
            or (eligibility.payout_kyc_required and not eligibility.payout_kyc_satisfied)
            or performer_consent_issue_count > 0
        ),
        rejection_reason=profile.rejection_reason,
        languages=[
            TaxonomyItem(id=row.id, code=row.code, label=row.label)
            for row in sorted(profile.languages, key=lambda item: item.code)
        ],
        categories=[
            TaxonomyItem(id=row.id, code=row.slug, label=row.label)
            for row in sorted(profile.categories, key=lambda item: (item.position, item.slug))
        ],
        social_links=[
            SocialLinkInput(label=row.label, url=row.url)
            for row in sorted(profile.links, key=lambda item: (item.position, str(item.id)))
        ],
        available_languages=[
            TaxonomyItem(id=row.id, code=row.code, label=row.label) for row in available_languages
        ],
        available_categories=[
            TaxonomyItem(id=row.id, code=row.slug, label=row.label) for row in available_categories
        ],
        development_verification_available=development_verification_available,
        staging_kyc_sandbox_available=staging_kyc_sandbox_available,
        staging_kyc_session_reference=(
            verification.provider_reference
            if verification
            and verification.provider == "staging_sandbox"
            and verification.status.value == "pending"
            else None
        ),
        staging_kyc_verification_id=(
            verification.id
            if verification
            and verification.provider == "staging_sandbox"
            and verification.status.value == "pending"
            else None
        ),
    )


@router.post("/me/application", response_model=CreatorSelfResponse)
async def start_application(
    request: Request, identity: CurrentIdentity, db: Db
) -> CreatorSelfResponse:
    await require_creator_registration(db, request, identity[0])
    profile = await service.get_or_create_profile(db, identity[0])
    await db.commit()
    await db.refresh(profile, ["categories", "languages", "links"])
    return await self_response(db, profile)


@router.get("/me", response_model=CreatorSelfResponse)
async def own_profile(identity: CurrentIdentity, db: Db) -> CreatorSelfResponse:
    profile = await service.profile_for_user(db, identity[0].id)
    if not profile:
        raise HTTPException(status_code=404, detail="Creator application not found")
    return await self_response(db, profile)


@router.get("/me/username-availability", response_model=CreatorUsernameAvailabilityResponse)
async def own_username_availability(
    username: str, request: Request, identity: CurrentIdentity, db: Db
) -> CreatorUsernameAvailabilityResponse:
    """Check a public creator handle while retaining server-side uniqueness on save."""
    await enforce_discovery_rate_limit(request, str(identity[0].id))
    profile = await service.profile_for_user(db, identity[0].id)
    normalized, available = await service.username_availability(
        db, username, creator_profile_id=profile.id if profile else None
    )
    return CreatorUsernameAvailabilityResponse(username=normalized, available=available)


@router.patch("/me", response_model=CreatorSelfResponse)
async def update_own_profile(
    payload: CreatorProfileUpdate,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
) -> CreatorSelfResponse:
    await require_creator_registration(db, request, identity[0])
    profile = await service.profile_for_user(db, identity[0].id)
    if not profile:
        raise HTTPException(status_code=404, detail="Creator application not found")
    try:
        await service.update_profile(
            db, profile, payload.model_dump(exclude_unset=True), identity[0].id
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.refresh(profile, ["categories", "languages", "links"])
    return await self_response(db, profile)


@router.post("/me/submit", response_model=CreatorSelfResponse)
async def submit_application(
    request: Request, identity: CurrentIdentity, db: Db
) -> CreatorSelfResponse:
    await require_creator_registration(db, request, identity[0])
    profile = await service.profile_for_user(db, identity[0].id)
    if not profile:
        raise HTTPException(status_code=404, detail="Creator application not found")
    try:
        await service.submit(db, profile, identity[0].id)
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await self_response(db, profile)


@router.post("/me/verification/development", response_model=CreatorSelfResponse)
async def development_verification(
    request: Request, identity: CurrentIdentity, db: Db, adult: bool = True
) -> CreatorSelfResponse:
    await require_creator_registration(db, request, identity[0])
    if not development_verification_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    profile = await service.profile_for_user(db, identity[0].id)
    if not profile:
        raise HTTPException(status_code=404, detail="Creator application not found")
    try:
        await service.development_verify(db, profile, adult, identity[0].id)
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await self_response(db, profile)


@router.post("/me/verification/staging-sandbox/start", response_model=CreatorSelfResponse)
async def start_staging_kyc_verification(
    request: Request, identity: CurrentIdentity, db: Db
) -> CreatorSelfResponse:
    await require_creator_registration(db, request, identity[0])
    if not staging_kyc_sandbox_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    profile = await service.profile_for_user(db, identity[0].id)
    if not profile:
        raise HTTPException(status_code=404, detail="Creator application not found")
    try:
        await service.start_staging_kyc(db, profile, identity[0].id)
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await self_response(db, profile)


@router.post("/me/verification/staging-sandbox/{verification_id}/complete", status_code=202)
async def complete_staging_kyc_verification(
    verification_id: UUID,
    payload: StagingKycOutcomeInput,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
) -> dict[str, str]:
    await require_creator_registration(db, request, identity[0])
    if not staging_kyc_sandbox_enabled():
        raise HTTPException(status_code=404, detail="Not found")
    verification = await db.get(CreatorVerification, verification_id, with_for_update=True)
    if not verification:
        raise HTTPException(status_code=404, detail="Creator KYC session not found")
    profile = await service.profile_for_user(db, identity[0].id)
    if not profile or verification.creator_profile_id != profile.id:
        raise HTTPException(status_code=404, detail="Creator KYC session not found")
    try:
        event = await service.queue_staging_kyc_outcome(db, verification, payload.outcome)
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "queued", "event_id": event.external_event_id}


@router.post("/webhooks/staging-sandbox", status_code=204)
async def staging_kyc_webhook(request: Request, db: Db) -> None:
    if request.headers.get("content-length") and int(request.headers["content-length"]) > 65536:
        raise HTTPException(status_code=413, detail="Webhook payload is too large")
    payload = await request.body()
    if len(payload) > 65536:
        raise HTTPException(status_code=413, detail="Webhook payload is too large")
    try:
        await service.process_staging_kyc_webhook(
            db, payload, request.headers.get("X-Kyc-Signature")
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{username}", response_model=PublicCreatorResponse)
async def public_profile(
    username: str, request: Request, db: Db, identity: OptionalIdentity
) -> PublicCreatorResponse:
    profile = await db.scalar(
        select(CreatorProfile).where(
            CreatorProfile.username == username.lower(),
        )
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Creator not found")
    try:
        await service.require_public_creator_access(
            db, profile.id, identity[0].id if identity else None
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Creator not found") from exc
    decision = await request_creator_decision(
        db, request, identity[0] if identity else None, registration=False
    )
    compliance_allowed = decision.allowed
    location = None
    if compliance_allowed and profile.show_location:
        location = (
            ", ".join(item for item in (profile.city, profile.region, profile.country_code) if item)
            or None
        )
    return PublicCreatorResponse(
        id=profile.id,
        username=profile.username,
        display_name=profile.display_name or profile.username,
        bio=profile.bio if compliance_allowed else None,
        avatar_reference=(
            safe_public_profile_media_reference(profile.avatar_reference)
            if compliance_allowed
            else None
        ),
        cover_reference=(
            safe_public_profile_media_reference(profile.cover_reference)
            if compliance_allowed
            else None
        ),
        location=location,
        timezone=profile.timezone if compliance_allowed else None,
        verified=True,
        follower_count=(
            int(
                await db.scalar(
                    select(func.count()).select_from(Follow).where(Follow.creator_id == profile.id)
                )
                or 0
            )
            if compliance_allowed
            else 0
        ),
        languages=(
            [
                TaxonomyItem(id=row.id, code=row.code, label=row.label)
                for row in sorted(profile.languages, key=lambda item: item.code)
            ]
            if compliance_allowed
            else []
        ),
        categories=(
            [
                TaxonomyItem(id=row.id, code=row.slug, label=row.label)
                for row in sorted(profile.categories, key=lambda item: (item.position, item.slug))
            ]
            if compliance_allowed
            else []
        ),
        social_links=(
            [
                SocialLinkInput(label=row.label, url=row.url)
                for row in sorted(profile.links, key=lambda item: (item.position, str(item.id)))
            ]
            if compliance_allowed
            else []
        ),
        adult_access_required=True,
        adult_access_granted=decision.age_access_allowed,
        compliance_allowed=compliance_allowed,
        compliance_code=decision.code,
        compliance_action=decision.action if not compliance_allowed else None,
        compliance_reason=decision.reason,
    )
