from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.compliance.policy import effective_policy_for_country
from app.compliance.types import ASSURANCE_STRENGTH, normalize_country_code
from app.creators.service import resolve_creator_compliance_eligibility
from app.media.service import approved_creator
from app.models.compliance import (
    AgeAssuranceLevel,
    AgeVerificationStatus,
    PerformerAgeVerification,
    PerformerIdentity,
    PerformerIdentityStatus,
    PerformerIdentityVerification,
    VerifiedContentPerformer,
)
from app.models.content import ContentItem
from app.models.identity import User
from app.models.trust_safety import ConsentRelease, ConsentReleaseContent


class PerformerError(ValueError):
    pass


async def create_identity(
    db: AsyncSession,
    actor: User,
    safe_reference: str,
    *,
    platform_user_id: UUID | None = None,
    country_code: str | None = None,
) -> PerformerIdentity:
    creator = await approved_creator(db, actor)
    reference = " ".join(safe_reference.split())
    if not 2 <= len(reference) <= 255:
        raise PerformerError("A safe performer reference is required")
    if await db.scalar(
        select(PerformerIdentity.id).where(
            PerformerIdentity.owner_creator_id == creator.id,
            PerformerIdentity.safe_reference == reference,
        )
    ):
        raise PerformerError("Performer reference already exists")
    if platform_user_id is not None and platform_user_id != actor.id:
        actor_roles = {role.name for role in actor.roles}
        if not actor_roles.intersection({"admin", "super_admin"}):
            raise PerformerError(
                "A creator may only attach their own platform account to a performer"
            )
        if await db.get(User, platform_user_id) is None:
            raise PerformerError("Platform user not found")
    performer = PerformerIdentity(
        owner_creator_id=creator.id,
        platform_user_id=platform_user_id,
        safe_reference=reference,
        country_code=country_code.upper() if country_code else None,
        created_by_user_id=actor.id,
    )
    db.add(performer)
    await db.flush()
    await record_event(
        db,
        "performer.identity_created",
        actor_user_id=actor.id,
        target_type="performer_identity",
        target_id=str(performer.id),
    )
    return performer


async def owned_identities(db: AsyncSession, actor: User) -> list[PerformerIdentity]:
    creator = await approved_creator(db, actor)
    return list(
        await db.scalars(
            select(PerformerIdentity)
            .where(PerformerIdentity.owner_creator_id == creator.id)
            .order_by(PerformerIdentity.created_at.desc())
        )
    )


async def link_content_performer(
    db: AsyncSession,
    actor: User,
    content_id: UUID,
    performer_id: UUID,
    consent_release_id: UUID | None,
    *,
    identity_verification_required: bool = True,
    age_verification_required: bool = True,
    release_required: bool = True,
) -> VerifiedContentPerformer:
    creator = await approved_creator(db, actor)
    content = await db.get(ContentItem, content_id)
    performer = await db.get(PerformerIdentity, performer_id)
    if (
        content is None
        or content.owner_creator_id != creator.id
        or performer is None
        or performer.owner_creator_id != creator.id
    ):
        raise PerformerError("Content performer scope is invalid")
    creator_eligibility = await resolve_creator_compliance_eligibility(
        db,
        profile=creator,
    )
    creator_policy = (
        await effective_policy_for_country(db, creator_eligibility.jurisdiction)
        if creator_eligibility.jurisdiction is not None
        else None
    )
    if creator_policy is None:
        raise PerformerError("No reviewed effective creator performer policy is available")
    linked_user = (
        await db.get(User, performer.platform_user_id)
        if performer.platform_user_id is not None
        else None
    )
    countries = {
        country
        for country in (
            normalize_country_code(performer.country_code),
            normalize_country_code(linked_user.country_code if linked_user else None),
            normalize_country_code(actor.country_code),
        )
        if country is not None
    }
    if len(countries) != 1:
        raise PerformerError("Performer jurisdiction could not be resolved without conflict")
    policy = await effective_policy_for_country(db, countries.pop())
    if policy is None:
        raise PerformerError("No reviewed effective performer policy is available")

    # These values snapshot the reviewed jurisdiction policy. They are not
    # creator-controlled switches. The legacy content flag may strengthen,
    # but never weaken, the performer-specific release requirement.
    strict_creator_performer_authority = creator_policy.rules.co_performer_verification_required
    required_identity = (
        strict_creator_performer_authority or policy.rules.co_performer_verification_required
    )
    required_age = required_identity
    required_release = (
        strict_creator_performer_authority
        or creator_policy.rules.release_required
        or policy.rules.release_required
        or content.requires_verified_consent
    )
    if (
        (required_identity and not identity_verification_required)
        or (required_age and not age_verification_required)
        or (required_release and not release_required)
    ):
        raise PerformerError("Performer compliance requirements cannot be weakened")
    release = await db.get(ConsentRelease, consent_release_id) if consent_release_id else None
    if required_release and (
        release is None
        or release.owner_creator_id != creator.id
        or release.participant_reference != performer.safe_reference
        or not await db.scalar(
            select(ConsentReleaseContent.consent_release_id).where(
                ConsentReleaseContent.consent_release_id == release.id,
                ConsentReleaseContent.content_id == content.id,
            )
        )
    ):
        raise PerformerError("A performer-specific release scoped to this content is required")
    link = await db.scalar(
        select(VerifiedContentPerformer).where(
            VerifiedContentPerformer.content_id == content.id,
            VerifiedContentPerformer.performer_id == performer.id,
        )
    )
    if link is None:
        link = VerifiedContentPerformer(
            content_id=content.id,
            performer_id=performer.id,
            consent_release_id=consent_release_id,
            identity_verification_required=required_identity,
            age_verification_required=required_age,
            release_required=required_release,
        )
        db.add(link)
        await db.flush()
    await record_event(
        db,
        "performer.content_linked",
        actor_user_id=actor.id,
        target_type="content_item",
        target_id=str(content.id),
        metadata={"performer_id": str(performer.id)},
    )
    return link


async def record_identity_verification(
    db: AsyncSession,
    reviewer: User,
    performer_id: UUID,
    *,
    provider: str,
    provider_reference: str,
    status: PerformerIdentityStatus,
    country_code: str | None,
    expires_at: datetime | None,
    confirmed: bool,
    reason: str,
) -> PerformerIdentityVerification:
    review_reason = " ".join(reason.split())
    if not confirmed or len(review_reason) < 8:
        raise PerformerError("Explicit confirmation and a review reason are required")
    if await db.get(PerformerIdentity, performer_id) is None:
        raise PerformerError("Performer identity not found")
    now = datetime.now(UTC)
    row = PerformerIdentityVerification(
        performer_id=performer_id,
        provider=provider,
        provider_reference=provider_reference,
        status=status,
        country_code=country_code.upper() if country_code else None,
        verified_at=now if status is PerformerIdentityStatus.verified else None,
        expires_at=expires_at,
        metadata_json={"manual_review": True, "review_reason": review_reason},
    )
    db.add(row)
    await db.flush()
    await record_event(
        db,
        "performer.identity_verification_recorded",
        actor_user_id=reviewer.id,
        target_type="performer_identity",
        target_id=str(performer_id),
        metadata={
            "status": status.value,
            "provider": provider,
            "confirmed": True,
            "reason": review_reason,
        },
    )
    return row


async def record_age_verification(
    db: AsyncSession,
    reviewer: User,
    performer_id: UUID,
    *,
    provider: str,
    provider_reference: str,
    status: AgeVerificationStatus,
    country_code: str,
    required_minimum_age: int,
    achieved_assurance_level: AgeAssuranceLevel,
    expires_at: datetime | None,
    confirmed: bool,
    reason: str,
) -> PerformerAgeVerification:
    review_reason = " ".join(reason.split())
    if not confirmed or len(review_reason) < 8:
        raise PerformerError("Explicit confirmation and a review reason are required")
    performer = await db.get(PerformerIdentity, performer_id)
    if performer is None:
        raise PerformerError("Performer identity not found")
    now = datetime.now(UTC)
    verification_country = normalize_country_code(country_code)
    performer_country = normalize_country_code(performer.country_code)
    if verification_country is None:
        raise PerformerError("A valid performer verification country is required")
    if performer_country is not None and performer_country != verification_country:
        raise PerformerError("Performer verification country conflicts with the performer record")
    policy = await effective_policy_for_country(db, verification_country)
    if policy is None:
        raise PerformerError("No reviewed effective performer policy is available")
    if status is AgeVerificationStatus.verified:
        if required_minimum_age < policy.rules.minimum_age:
            raise PerformerError("Verified age threshold is below the current performer policy")
        if (
            ASSURANCE_STRENGTH[achieved_assurance_level]
            < ASSURANCE_STRENGTH[policy.rules.required_assurance_level]
        ):
            raise PerformerError("Age assurance is below the current performer policy")
        if expires_at is not None and expires_at.tzinfo is None:
            raise PerformerError("Verification expiry must include a timezone")
        if expires_at is not None and expires_at <= now:
            raise PerformerError("Verified performer age evidence is already expired")
        if policy.rules.reverify_after_days is not None and expires_at is None:
            raise PerformerError("Current performer policy requires finite verification validity")
    row = PerformerAgeVerification(
        performer_id=performer_id,
        provider=provider,
        provider_reference=provider_reference,
        status=status,
        country_code=verification_country,
        required_minimum_age=required_minimum_age,
        achieved_assurance_level=achieved_assurance_level,
        verified_at=now if status is AgeVerificationStatus.verified else None,
        expires_at=expires_at,
        metadata_json={
            "manual_review": True,
            "review_reason": review_reason,
            "verified_minimum_age_threshold": required_minimum_age,
        },
    )
    db.add(row)
    await db.flush()
    await record_event(
        db,
        "performer.age_verification_recorded",
        actor_user_id=reviewer.id,
        target_type="performer_identity",
        target_id=str(performer_id),
        metadata={
            "status": status.value,
            "provider": provider,
            "confirmed": True,
            "reason": review_reason,
        },
    )
    return row
