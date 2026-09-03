import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.accounts.service import assign_role
from app.audit.service import record_event
from app.compliance.policy import effective_policy_for_country
from app.compliance.types import JurisdictionSignals, resolve_jurisdiction_candidates
from app.core.config import get_settings
from app.media.contexts import require_media_context_available
from app.models.compliance import CountryRegistry
from app.models.content import (
    DerivativeType,
    MediaAsset,
    MediaAudience,
    MediaDerivative,
    MediaStatus,
    MediaType,
    ModerationStatus,
)
from app.models.creator import (
    CreatorCategory,
    CreatorKycWebhookEvent,
    CreatorLanguage,
    CreatorProfile,
    CreatorProfileMedia,
    CreatorSocialLink,
    CreatorStatus,
    CreatorStatusHistory,
    CreatorUsernameHistory,
    CreatorVerification,
    StagingCreatorKycSandboxEvent,
    VerificationStatus,
)
from app.models.identity import Role, User
from app.models.messaging import UserBlock
from app.notifications.service import emit_transactional

RESERVED_USERNAMES = frozenset(
    {
        "admin",
        "api",
        "login",
        "register",
        "account",
        "creator",
        "creators",
        "live",
        "stories",
        "market",
        "marketplace",
        "support",
        "help",
        "billing",
        "settings",
    }
)
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_-]{2,31}$")
TRANSITIONS = {
    CreatorStatus.draft: {CreatorStatus.pending_verification},
    CreatorStatus.pending_verification: {CreatorStatus.pending_review},
    CreatorStatus.pending_review: {CreatorStatus.approved, CreatorStatus.rejected},
    CreatorStatus.approved: {CreatorStatus.suspended, CreatorStatus.disabled},
    CreatorStatus.rejected: {CreatorStatus.draft},
    CreatorStatus.suspended: {CreatorStatus.approved, CreatorStatus.disabled},
    CreatorStatus.disabled: {CreatorStatus.draft},
}


async def profile_media(db: AsyncSession, creator_id: UUID) -> list[CreatorProfileMedia]:
    return list(
        await db.scalars(
            select(CreatorProfileMedia)
            .where(CreatorProfileMedia.creator_profile_id == creator_id)
            .order_by(CreatorProfileMedia.kind)
        )
    )


async def set_profile_media(
    db: AsyncSession,
    profile: CreatorProfile,
    actor_user_id: UUID,
    *,
    kind: str,
    media_asset_id: UUID,
    focal_x: float,
    focal_y: float,
) -> CreatorProfileMedia:
    if kind not in {"avatar", "cover"}:
        raise ValueError("Profile media kind is invalid")
    asset = await db.scalar(
        select(MediaAsset)
        .where(
            MediaAsset.id == media_asset_id,
            MediaAsset.owner_creator_id == profile.id,
            MediaAsset.media_type == MediaType.image,
            MediaAsset.status == MediaStatus.ready,
            MediaAsset.moderation_status == ModerationStatus.approved,
            MediaAsset.audience == MediaAudience.safe_public,
            MediaAsset.deleted_at.is_(None),
            select(MediaDerivative.id)
            .where(
                MediaDerivative.media_asset_id == MediaAsset.id,
                MediaDerivative.derivative_type == DerivativeType.display,
                MediaDerivative.status == MediaStatus.ready,
            )
            .exists(),
        )
        .with_for_update()
    )
    if asset is None:
        raise ValueError("Profile media must be a ready, approved, safe-public image")
    current = await db.scalar(
        select(CreatorProfileMedia)
        .where(
            CreatorProfileMedia.creator_profile_id == profile.id,
            CreatorProfileMedia.kind == kind,
        )
        .with_for_update()
    )
    await require_media_context_available(
        db,
        asset.id,
        context_type="profile",
        context_id=current.id if current and current.media_asset_id == asset.id else None,
    )
    if current is None:
        current = CreatorProfileMedia(
            creator_profile_id=profile.id, media_asset_id=asset.id, kind=kind
        )
        db.add(current)
        await db.flush()
    else:
        current.media_asset_id = asset.id
    current.focal_x = focal_x
    current.focal_y = focal_y
    await record_event(
        db,
        "creator.profile_media_updated",
        actor_user_id=actor_user_id,
        target_type="creator_profile",
        target_id=str(profile.id),
        metadata={"kind": kind, "media_asset_id": str(asset.id)},
    )
    return current


async def remove_profile_media(
    db: AsyncSession, profile: CreatorProfile, actor_user_id: UUID, *, kind: str
) -> bool:
    row = await db.scalar(
        select(CreatorProfileMedia)
        .where(
            CreatorProfileMedia.creator_profile_id == profile.id,
            CreatorProfileMedia.kind == kind,
        )
        .with_for_update()
    )
    if row is None:
        return False
    await db.delete(row)
    await record_event(
        db,
        "creator.profile_media_removed",
        actor_user_id=actor_user_id,
        target_type="creator_profile",
        target_id=str(profile.id),
        metadata={"kind": kind},
    )
    return True


@dataclass(frozen=True)
class CreatorComplianceEligibility:
    jurisdiction: str | None
    policy_version: int | None
    verification_status: str | None
    verification_expires_at: datetime | None
    identity_required: bool
    age_required: bool
    payout_kyc_required: bool
    identity_allowed: bool
    age_allowed: bool
    public_allowed: bool
    payout_kyc_satisfied: bool
    payout_allowed: bool
    code: str
    reason: str
    payout_code: str


def normalize_username(value: str) -> str:
    normalized = value.strip().lower()
    if not USERNAME_RE.fullmatch(normalized) or normalized in RESERVED_USERNAMES:
        raise ValueError("Username is unavailable or invalid")
    return normalized


async def username_availability(
    db: AsyncSession, value: str, *, creator_profile_id: UUID | None = None
) -> tuple[str, bool]:
    """Return public-handle availability without weakening final write validation.

    Creator handles are public identifiers, but a creator may keep their own current
    handle. Historic handles remain reserved so a public profile URL cannot be
    reassigned to a different creator later.
    """
    try:
        username = normalize_username(value)
    except ValueError:
        return value.strip().lower(), False
    owner = await db.scalar(
        select(CreatorUsernameHistory).where(CreatorUsernameHistory.username == username)
    )
    return username, owner is None or owner.creator_profile_id == creator_profile_id


async def profile_for_user(db: AsyncSession, user_id: UUID) -> CreatorProfile | None:
    return await db.scalar(select(CreatorProfile).where(CreatorProfile.user_id == user_id))


def current_adult_verification_predicate(creator_profile_id):
    """Correlated latest-outcome predicate shared by every public creator projection."""

    latest = aliased(CreatorVerification)
    candidate = aliased(CreatorVerification)
    latest_id = (
        select(latest.id)
        .where(latest.creator_profile_id == creator_profile_id)
        .order_by(latest.created_at.desc(), latest.id.desc())
        .limit(1)
        .correlate_except(latest)
        .scalar_subquery()
    )
    return exists(
        select(candidate.id).where(
            candidate.id == latest_id,
            candidate.status == VerificationStatus.verified,
            candidate.adult_verified.is_(True),
            # Legacy query consumers cannot interpret jurisdiction JSON. Keep
            # them conservatively stricter until they use the policy resolver.
            candidate.identity_verified.is_(True),
            candidate.revoked_at.is_(None),
            candidate.expires_at.is_not(None),
            candidate.expires_at > func.now(),
        )
    )


def current_identity_verification_predicate(creator_profile_id):
    """Correlated latest normalized identity/KYC outcome predicate."""

    latest = aliased(CreatorVerification)
    candidate = aliased(CreatorVerification)
    latest_id = (
        select(latest.id)
        .where(latest.creator_profile_id == creator_profile_id)
        .order_by(latest.created_at.desc(), latest.id.desc())
        .limit(1)
        .correlate_except(latest)
        .scalar_subquery()
    )
    return exists(
        select(candidate.id).where(
            candidate.id == latest_id,
            candidate.status == VerificationStatus.verified,
            candidate.identity_verified.is_(True),
            candidate.revoked_at.is_(None),
            candidate.expires_at.is_not(None),
            candidate.expires_at > func.now(),
        )
    )


async def latest_verification(
    db: AsyncSession, creator_profile_id: UUID
) -> CreatorVerification | None:
    return await db.scalar(
        select(CreatorVerification)
        .where(CreatorVerification.creator_profile_id == creator_profile_id)
        .order_by(CreatorVerification.created_at.desc(), CreatorVerification.id.desc())
        .limit(1)
    )


async def review_creator_kyc(
    db: AsyncSession,
    *,
    verification_id: UUID,
    reviewer: User,
    action: str,
    reason: str,
    expected_status: VerificationStatus = VerificationStatus.needs_review,
) -> CreatorVerification:
    """Resolve provider-requested human review without inventing identity authority.

    Manual approval is deliberately unsupported: only a signed provider result
    may establish verified identity/adulthood. Operators may reject, request a
    new provider session, or retain review state with an audited note.
    """
    verification = await db.scalar(
        select(CreatorVerification)
        .where(CreatorVerification.id == verification_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if verification is None:
        raise ValueError("Creator KYC review not found")
    if verification.status is not expected_status:
        raise ValueError("Creator KYC review changed; refresh before deciding")
    clean_reason = reason.strip()
    if len(clean_reason) < 8:
        raise ValueError("A review reason of at least 8 characters is required")
    if action not in {"reject", "request_reverification", "leave_in_review"}:
        raise ValueError("Manual creator KYC approval is not permitted")
    profile = await db.scalar(
        select(CreatorProfile)
        .where(CreatorProfile.id == verification.creator_profile_id)
        .with_for_update()
    )
    if profile is None:
        raise ValueError("Creator profile not found")
    if action != "leave_in_review":
        verification.status = VerificationStatus.failed
        verification.identity_verified = False
        verification.adult_verified = False
        verification.failure_reason_code = (
            "manual_review_rejected" if action == "reject" else "reverification_requested"
        )
        verification.verified_at = None
        verification.expires_at = None
        verification.revoked_at = None
        if profile.status is not CreatorStatus.pending_verification:
            previous = profile.status
            profile.status = CreatorStatus.pending_verification
            db.add(
                CreatorStatusHistory(
                    creator_profile_id=profile.id,
                    previous_status=previous,
                    new_status=profile.status,
                    actor_user_id=reviewer.id,
                    reason="Creator identity review requires a new provider outcome",
                )
            )
    await record_event(
        db,
        "creator.verification_manual_reviewed",
        actor_user_id=reviewer.id,
        target_type="creator_verification",
        target_id=str(verification.id),
        metadata={
            "action": action,
            "reason": clean_reason,
            "provider": verification.provider,
            "creator_profile_id": str(profile.id),
        },
    )
    await emit_transactional(
        db,
        recipient_user_id=profile.user_id,
        notification_type="CREATOR_KYC_REVIEWED",
        source_domain="creators",
        source_id=str(verification.id),
        title="Identity review updated",
        body=(
            "Your identity review remains with our review team."
            if action == "leave_in_review"
            else "Your identity review needs another step in FanBackstage."
        ),
        target_path="/creator-onboarding",
    )
    return verification


async def resolve_creator_compliance_eligibility(
    db: AsyncSession,
    *,
    profile: CreatorProfile,
    now: datetime | None = None,
) -> CreatorComplianceEligibility:
    """Resolve creator identity/adult eligibility separately from fan assurance."""

    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    verification = await latest_verification(db, profile.id)
    account = await db.get(User, profile.user_id)
    current_kyc_country = (
        verification.country_code
        if verification
        and verification.status is VerificationStatus.verified
        and verification.verified_at is not None
        and verification.revoked_at is None
        and verification.expires_at is not None
        and verification.expires_at > current
        else None
    )
    try:
        countries = resolve_jurisdiction_candidates(
            JurisdictionSignals(
                kyc_country=current_kyc_country,
                account_country=account.country_code if account else None,
            ),
            fallback_country=get_settings().effective_compliance_fallback_country(),
            allow_untrusted_selection=False,
        )
    except ValueError:
        countries = ()
    if len(countries) != 1:
        return CreatorComplianceEligibility(
            jurisdiction=countries[0] if countries else None,
            policy_version=None,
            verification_status=verification.status.value if verification else None,
            verification_expires_at=None,
            identity_required=True,
            age_required=True,
            payout_kyc_required=True,
            identity_allowed=False,
            age_allowed=False,
            public_allowed=False,
            payout_kyc_satisfied=False,
            payout_allowed=False,
            code=(
                "CREATOR_COUNTRY_CONFLICT"
                if len(countries) > 1
                else "CREATOR_JURISDICTION_UNRESOLVED"
            ),
            reason="Creator jurisdiction is unresolved or conflicting",
            payout_code="PAYOUT_COMPLIANCE_UNAVAILABLE",
        )
    country = countries[0]
    registry = await db.get(CountryRegistry, country)
    if registry is None or not registry.enabled:
        return CreatorComplianceEligibility(
            jurisdiction=country,
            policy_version=None,
            verification_status=verification.status.value if verification else None,
            verification_expires_at=None,
            identity_required=True,
            age_required=True,
            payout_kyc_required=True,
            identity_allowed=False,
            age_allowed=False,
            public_allowed=False,
            payout_kyc_satisfied=False,
            payout_allowed=False,
            code="CREATOR_JURISDICTION_BLOCKED",
            reason="Creator access is unavailable in this jurisdiction",
            payout_code="PAYOUT_COMPLIANCE_UNAVAILABLE",
        )
    policy = await effective_policy_for_country(db, country, now=current)
    if policy is None or not policy.rules.enabled:
        return CreatorComplianceEligibility(
            jurisdiction=country,
            policy_version=None,
            verification_status=verification.status.value if verification else None,
            verification_expires_at=None,
            identity_required=True,
            age_required=True,
            payout_kyc_required=True,
            identity_allowed=False,
            age_allowed=False,
            public_allowed=False,
            payout_kyc_satisfied=False,
            payout_allowed=False,
            code="CREATOR_POLICY_UNAVAILABLE",
            reason="No reviewed creator compliance policy is available",
            payout_code="PAYOUT_COMPLIANCE_UNAVAILABLE",
        )
    rules = policy.rules
    expiry_bounds: list[datetime] = []
    if verification and verification.expires_at is not None:
        expiry_bounds.append(verification.expires_at)
    if (
        verification
        and verification.verified_at is not None
        and rules.reverify_after_days is not None
    ):
        expiry_bounds.append(verification.verified_at + timedelta(days=rules.reverify_after_days))
    effective_expiry = min(expiry_bounds) if expiry_bounds else None
    verification_current = bool(
        verification
        and verification.status is VerificationStatus.verified
        and verification.verified_at is not None
        and verification.revoked_at is None
        and effective_expiry is not None
        and effective_expiry > current
    )
    identity_allowed = not rules.creator_identity_required or bool(
        verification_current and verification and verification.identity_verified
    )
    age_allowed = not rules.creator_age_verification_required or bool(
        verification_current and verification and verification.adult_verified
    )
    payout_kyc_satisfied = not rules.payout_kyc_required or bool(
        verification_current and verification and verification.identity_verified
    )
    if not identity_allowed:
        code = "CREATOR_IDENTITY_VERIFICATION_REQUIRED"
        reason = "Current creator identity verification is required"
    elif not age_allowed:
        code = "CREATOR_AGE_VERIFICATION_REQUIRED"
        reason = "Current creator adult verification is required"
    else:
        code = "CREATOR_COMPLIANCE_ALLOWED"
        reason = "Creator identity and age requirements are satisfied"
    return CreatorComplianceEligibility(
        jurisdiction=country,
        policy_version=policy.jurisdiction_revision.version,
        verification_status=verification.status.value if verification else None,
        verification_expires_at=effective_expiry,
        identity_required=rules.creator_identity_required,
        age_required=rules.creator_age_verification_required,
        payout_kyc_required=rules.payout_kyc_required,
        identity_allowed=identity_allowed,
        age_allowed=age_allowed,
        public_allowed=identity_allowed and age_allowed,
        payout_kyc_satisfied=payout_kyc_satisfied,
        # No payout rail exists yet. Never present policy/KYC satisfaction as
        # operational payout eligibility.
        payout_allowed=False,
        code=code,
        reason=reason,
        payout_code=("PAYOUT_NOT_CONFIGURED" if payout_kyc_satisfied else "PAYOUT_KYC_REQUIRED"),
    )


async def resolve_creator_compliance_eligibilities(
    db: AsyncSession,
    *,
    profiles: Sequence[CreatorProfile],
    now: datetime | None = None,
) -> dict[UUID, CreatorComplianceEligibility]:
    """Resolve a pre-paginated creator list through the canonical policy path."""

    current = now or datetime.now(UTC)
    return {
        profile.id: await resolve_creator_compliance_eligibility(
            db,
            profile=profile,
            now=current,
        )
        for profile in profiles
    }


async def has_current_adult_verification(db: AsyncSession, creator_profile_id: UUID) -> bool:
    verification = await latest_verification(db, creator_profile_id)
    return bool(
        verification
        and verification.status is VerificationStatus.verified
        and verification.adult_verified
        and verification.revoked_at is None
        and verification.expires_at is not None
        and verification.expires_at > datetime.now(UTC)
    )


async def has_current_identity_verification(db: AsyncSession, creator_profile_id: UUID) -> bool:
    verification = await latest_verification(db, creator_profile_id)
    return bool(
        verification
        and verification.status is VerificationStatus.verified
        and verification.identity_verified
        and verification.revoked_at is None
        and verification.expires_at is not None
        and verification.expires_at > datetime.now(UTC)
    )


async def require_current_identity_verification(
    db: AsyncSession, creator_profile_id: UUID
) -> CreatorVerification:
    verification = await latest_verification(db, creator_profile_id)
    if not (
        verification
        and verification.status is VerificationStatus.verified
        and verification.identity_verified
        and verification.revoked_at is None
        and verification.expires_at is not None
        and verification.expires_at > datetime.now(UTC)
    ):
        raise ValueError("A current verified creator identity/KYC outcome is required")
    return verification


async def require_current_adult_verification(
    db: AsyncSession, creator_profile_id: UUID
) -> CreatorVerification:
    verification = await latest_verification(db, creator_profile_id)
    if not (
        verification
        and verification.status is VerificationStatus.verified
        and verification.adult_verified
        and verification.revoked_at is None
        and verification.expires_at is not None
        and verification.expires_at > datetime.now(UTC)
    ):
        raise ValueError("A current verified adult KYC outcome is required")
    return verification


async def require_public_creator_access(
    db: AsyncSession,
    creator_profile_id: UUID,
    viewer_user_id: UUID | None = None,
) -> CreatorProfile:
    """Require a currently public, approved, policy-eligible creator relationship.

    This is a pre-charge/public-surface invariant. It deliberately includes
    two-way blocks when a viewer is known so payment cannot create access that
    the serving layer will immediately contain.
    """
    profile = await db.scalar(
        select(CreatorProfile).where(
            CreatorProfile.id == creator_profile_id,
            CreatorProfile.status == CreatorStatus.approved,
            CreatorProfile.is_public.is_(True),
        )
    )
    if not profile:
        raise ValueError("Creator is not publicly available")
    eligibility = await resolve_creator_compliance_eligibility(db, profile=profile)
    if not eligibility.public_allowed:
        raise ValueError("Creator is not publicly available")
    if viewer_user_id is not None and viewer_user_id != profile.user_id:
        blocked = await db.scalar(
            select(UserBlock.id).where(
                or_(
                    (UserBlock.blocker_user_id == viewer_user_id)
                    & (UserBlock.blocked_user_id == profile.user_id),
                    (UserBlock.blocker_user_id == profile.user_id)
                    & (UserBlock.blocked_user_id == viewer_user_id),
                )
            )
        )
        if blocked:
            raise ValueError("Creator is not publicly available")
    return profile


async def get_or_create_profile(db: AsyncSession, user: User) -> CreatorProfile:
    profile = await profile_for_user(db, user.id)
    if profile:
        return profile

    created_profile_id = await db.scalar(
        insert(CreatorProfile)
        .values(id=uuid4(), user_id=user.id)
        .on_conflict_do_nothing(index_elements=[CreatorProfile.user_id])
        .returning(CreatorProfile.id)
    )
    profile = await profile_for_user(db, user.id)
    if profile is None:
        raise RuntimeError("Creator profile insert did not return a canonical profile")
    if created_profile_id is not None:
        await record_event(
            db,
            "creator.application_started",
            actor_user_id=user.id,
            target_type="creator_profile",
            target_id=str(profile.id),
        )
    return profile


async def set_status(
    db: AsyncSession,
    profile: CreatorProfile,
    status: CreatorStatus,
    actor_user_id: UUID | None,
    reason: str | None = None,
) -> None:
    # Creator publication and every live-room/session creation serialize on
    # this same row. Reloading under the lock prevents a stale approved object
    # from racing a concurrent suspension or creating delivery after the
    # suspension scan has completed.
    locked_profile = await db.scalar(
        select(CreatorProfile)
        .where(CreatorProfile.id == profile.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_profile is None:
        raise ValueError("Creator profile not found")
    profile = locked_profile
    if status not in TRANSITIONS.get(profile.status, set()):
        raise ValueError(f"Cannot transition from {profile.status.value} to {status.value}")
    if status is CreatorStatus.approved:
        eligibility = await resolve_creator_compliance_eligibility(db, profile=profile)
        if not eligibility.public_allowed:
            raise ValueError(
                f"Creator compliance requirements are not satisfied ({eligibility.code})"
            )
    previous = profile.status
    profile.status = status
    if status in {CreatorStatus.suspended, CreatorStatus.disabled, CreatorStatus.rejected}:
        profile.is_public = False
        # A DB-only suspension cannot disconnect an already-connected LiveKit
        # broadcaster. Apply provider controls immediately; failures are
        # audited and the scheduled authority reconciler retries them.
        from app.streaming.service import terminate_creator_active_live

        await terminate_creator_active_live(
            db,
            profile.id,
            reason=f"creator_status_{status.value}",
        )
    if status == CreatorStatus.rejected:
        profile.rejection_reason = reason or "Application was not approved"
    if status == CreatorStatus.approved:
        user = await db.get(User, profile.user_id)
        assert user is not None
        await assign_role(db, user, "creator", actor_user_id, None)
    if status in {CreatorStatus.pending_verification, CreatorStatus.pending_review}:
        # Admins can see the application from submission, but an approval
        # notification is deliberately emitted only after identity verification
        # has completed. Neither notification contains applicant PII or KYC evidence.
        reviewers = (
            await db.scalars(
                select(User)
                .join(User.roles)
                .where(Role.name.in_(("admin", "super_admin")))
                .distinct()
            )
        ).all()
        for reviewer in reviewers:
            await emit_transactional(
                db,
                recipient_user_id=reviewer.id,
                notification_type=(
                    "CREATOR_APPLICATION_REVIEW_REQUIRED"
                    if status is CreatorStatus.pending_review
                    else "CREATOR_APPLICATION_KYC_STARTED"
                ),
                source_domain="creator_application",
                source_id=str(profile.id),
                title=(
                    "Creator application ready for review"
                    if status is CreatorStatus.pending_review
                    else "Creator application awaiting identity verification"
                ),
                body=(
                    "A creator identity check is complete and an application is ready for an authorised decision."
                    if status is CreatorStatus.pending_review
                    else "A creator application has been submitted and is awaiting its identity-verification result."
                ),
                target_path=(
                    "/admin/creators?status=pending_review"
                    if status is CreatorStatus.pending_review
                    else "/admin/creators?status=pending_verification"
                ),
                email=False,
            )
    db.add(
        CreatorStatusHistory(
            creator_profile_id=profile.id,
            previous_status=previous,
            new_status=status,
            actor_user_id=actor_user_id,
            reason=reason,
        )
    )
    await record_event(
        db,
        f"creator.status_{status.value}",
        actor_user_id=actor_user_id,
        target_type="creator_profile",
        target_id=str(profile.id),
        metadata={"previous_status": previous.value},
    )


async def update_profile(
    db: AsyncSession, profile: CreatorProfile, values: dict, actor_user_id: UUID
) -> None:
    categories = None
    if "category_slugs" in values and values["category_slugs"] is not None:
        category_slugs = _normalised_unique_values(
            values["category_slugs"], "Category selections", max_length=48
        )
        categories_by_slug = {
            row.slug: row
            for row in (
                await db.scalars(
                    select(CreatorCategory).where(
                        CreatorCategory.enabled.is_(True),
                        CreatorCategory.slug.in_(category_slugs),
                    )
                )
            ).all()
        }
        if set(categories_by_slug) != set(category_slugs):
            raise ValueError("Category selections include unavailable values")
        categories = sorted(categories_by_slug.values(), key=lambda row: (row.position, row.slug))

    languages = None
    if "language_codes" in values and values["language_codes"] is not None:
        language_codes = _normalised_unique_values(
            values["language_codes"], "Language selections", max_length=10
        )
        languages_by_code = {
            row.code: row
            for row in (
                await db.scalars(
                    select(CreatorLanguage).where(
                        CreatorLanguage.enabled.is_(True),
                        CreatorLanguage.code.in_(language_codes),
                    )
                )
            ).all()
        }
        if set(languages_by_code) != set(language_codes):
            raise ValueError("Language selections include unavailable values")
        languages = sorted(languages_by_code.values(), key=lambda row: row.code)

    social_links = None
    if "social_links" in values and values["social_links"] is not None:
        social_links = _normalised_social_links(values["social_links"])

    if values.get("username") is not None:
        username = normalize_username(values["username"])
        if username != profile.username:
            if await db.scalar(
                select(CreatorUsernameHistory).where(CreatorUsernameHistory.username == username)
            ):
                raise ValueError("Username is unavailable or invalid")
            profile.username = username
            db.add(CreatorUsernameHistory(username=username, creator_profile_id=profile.id))
            await record_event(
                db,
                "creator.username_changed",
                actor_user_id=actor_user_id,
                target_type="creator_profile",
                target_id=str(profile.id),
                metadata={"username": username},
            )
    for key in (
        "display_name",
        "show_location",
        "is_public",
    ):
        if key in values and values[key] is not None:
            setattr(profile, key, values[key])
    for key in ("bio", "country_code", "region", "city", "timezone"):
        if key in values:
            setattr(profile, key, values[key])
    if profile.country_code:
        profile.country_code = profile.country_code.upper()
    if categories is not None:
        profile.categories = categories
    if languages is not None:
        profile.languages = languages
    if social_links is not None:
        existing_by_url = {link.url: link for link in profile.links}
        replacement_links = []
        for position, (label, url) in enumerate(social_links):
            link = existing_by_url.pop(url, None)
            if link is None:
                link = CreatorSocialLink(creator_profile_id=profile.id, url=url)
            link.label = label
            link.position = position
            replacement_links.append(link)
        profile.links = replacement_links
    if profile.is_public and profile.status != CreatorStatus.approved:
        raise ValueError("Only approved creators can make a profile public")


def _normalised_unique_values(values: list, field_name: str, *, max_length: int) -> list[str]:
    normalised = [str(value).strip().lower() for value in values]
    if len(normalised) > 12:
        raise ValueError(f"{field_name} cannot contain more than 12 values")
    if any(not value for value in normalised):
        raise ValueError(f"{field_name} cannot contain blank values")
    if any(len(value) > max_length for value in normalised):
        raise ValueError(f"{field_name} include an invalid value")
    if len(set(normalised)) != len(normalised):
        raise ValueError(f"{field_name} cannot contain duplicates")
    return normalised


def _normalised_social_links(values: list) -> list[tuple[str, str]]:
    if len(values) > 12:
        raise ValueError("Social links cannot contain more than 12 values")
    links: list[tuple[str, str]] = []
    urls: set[str] = set()
    for value in values:
        if isinstance(value, dict):
            label_value = value.get("label")
            url_value = value.get("url")
        else:
            label_value = getattr(value, "label", None)
            url_value = getattr(value, "url", None)
        label = str(label_value or "").strip()
        url = str(url_value or "").strip()
        if not label or not url:
            raise ValueError("Every social link requires a label and URL")
        if len(label) > 48 or len(url) > 512:
            raise ValueError("Social link label or URL is too long")
        if any(ord(character) < 32 for character in label):
            raise ValueError("Social link labels cannot contain control characters")
        if "\\" in url or any(character.isspace() or ord(character) < 32 for character in url):
            raise ValueError("Social links require a valid HTTP or HTTPS URL")
        try:
            parsed_url = urlsplit(url)
            _ = parsed_url.port
        except ValueError as exc:
            raise ValueError("Social links require a valid HTTP or HTTPS URL") from exc
        if (
            parsed_url.scheme.lower() not in {"http", "https"}
            or not parsed_url.hostname
            or parsed_url.username is not None
            or parsed_url.password is not None
        ):
            raise ValueError("Social links require a valid HTTP or HTTPS URL")
        if url in urls:
            raise ValueError("Social link URLs cannot be duplicated")
        urls.add(url)
        links.append((label, url))
    return links


async def submit(db: AsyncSession, profile: CreatorProfile, actor_user_id: UUID) -> None:
    if not profile.username or not profile.display_name:
        raise ValueError("Username and display name are required before submitting")
    await set_status(db, profile, CreatorStatus.pending_verification, actor_user_id)
    await record_event(
        db,
        "creator.application_submitted",
        actor_user_id=actor_user_id,
        target_type="creator_profile",
        target_id=str(profile.id),
    )


async def development_verify(
    db: AsyncSession, profile: CreatorProfile, adult: bool, actor_user_id: UUID
) -> CreatorVerification:
    if profile.status is not CreatorStatus.pending_verification:
        raise ValueError("Development verification requires a pending creator verification")
    current = datetime.now(UTC)
    account = await db.get(User, profile.user_id)
    try:
        countries = resolve_jurisdiction_candidates(
            JurisdictionSignals(account_country=account.country_code if account else None),
            fallback_country=get_settings().effective_compliance_fallback_country(),
            allow_untrusted_selection=False,
        )
    except ValueError:
        countries = ()
    if len(countries) != 1:
        raise ValueError("Creator account jurisdiction is unresolved or conflicting")
    country_code = countries[0]
    policy = (
        await effective_policy_for_country(db, country_code, now=current) if country_code else None
    )
    validity_days = (
        policy.rules.reverify_after_days
        if policy and policy.rules.reverify_after_days is not None
        else get_settings().manual_age_review_max_days
    )
    verification = CreatorVerification(
        creator_profile_id=profile.id,
        provider="development",
        provider_reference=f"dev_{secrets.token_urlsafe(16)}",
        status=VerificationStatus.verified if adult else VerificationStatus.failed,
        adult_verified=adult,
        identity_verified=adult,
        country_code=country_code,
        verified_at=current if adult else None,
        expires_at=current + timedelta(days=validity_days) if adult else None,
        failure_reason_code=None if adult else "AGE_OR_IDENTITY_NOT_VERIFIED",
    )
    db.add(verification)
    await record_event(
        db,
        "creator.verification_changed",
        actor_user_id=actor_user_id,
        target_type="creator_profile",
        target_id=str(profile.id),
        metadata={"status": verification.status.value, "adult_verified": adult},
    )
    if adult and profile.status == CreatorStatus.pending_verification:
        await set_status(db, profile, CreatorStatus.pending_review, actor_user_id)
    return verification


_STAGING_KYC_OUTCOMES = {
    "VERIFIED": ("kyc.verified", VerificationStatus.verified),
    "FAILED": ("kyc.failed", VerificationStatus.failed),
    "REVIEW_REQUIRED": ("kyc.review_required", VerificationStatus.needs_review),
    "EXPIRED": ("kyc.expired", VerificationStatus.expired),
}


def _staging_kyc_signature(payload: bytes) -> str:
    return hmac.new(
        get_settings().staging_kyc_webhook_secret.encode(), payload, hashlib.sha256
    ).hexdigest()


async def start_staging_kyc(
    db: AsyncSession, profile: CreatorProfile, actor_user_id: UUID
) -> CreatorVerification:
    """Create a pending external-style KYC session without granting anything."""
    settings = get_settings()
    if (
        settings.environment not in {"staging", "test"}
        or settings.kyc_provider != "staging_sandbox"
    ):
        raise ValueError("Staging creator KYC sandbox is unavailable")
    if profile.status is not CreatorStatus.pending_verification:
        raise ValueError("Creator KYC requires a pending creator verification")
    existing = await latest_verification(db, profile.id)
    if (
        existing
        and existing.provider == "staging_sandbox"
        and existing.status is VerificationStatus.pending
    ):
        return existing
    account = await db.get(User, profile.user_id)
    try:
        countries = resolve_jurisdiction_candidates(
            JurisdictionSignals(account_country=account.country_code if account else None),
            fallback_country=settings.effective_compliance_fallback_country(),
            allow_untrusted_selection=False,
        )
    except ValueError:
        countries = ()
    if len(countries) != 1:
        raise ValueError("Creator account jurisdiction is unresolved or conflicting")
    verification = CreatorVerification(
        creator_profile_id=profile.id,
        provider="staging_sandbox",
        provider_reference=f"stgkyc_{secrets.token_urlsafe(18)}",
        status=VerificationStatus.pending,
        country_code=countries[0],
        metadata_json={"sandbox": "staging_test_only"},
    )
    db.add(verification)
    await db.flush()
    await record_event(
        db,
        "creator.kyc_started",
        actor_user_id=actor_user_id,
        target_type="creator_verification",
        target_id=str(verification.id),
        metadata={"provider": verification.provider},
    )
    await emit_transactional(
        db,
        recipient_user_id=profile.user_id,
        notification_type="CREATOR_KYC_STARTED",
        source_domain="creator_kyc",
        source_id=str(verification.id),
        title="Creator identity verification started",
        body="Your creator identity verification is pending.",
        target_path="/creator-studio",
    )
    return verification


async def queue_staging_kyc_outcome(
    db: AsyncSession, verification: CreatorVerification, outcome: str
) -> StagingCreatorKycSandboxEvent:
    if (
        verification.provider != "staging_sandbox"
        or verification.status is not VerificationStatus.pending
    ):
        raise ValueError("Creator KYC session is unavailable")
    if outcome not in _STAGING_KYC_OUTCOMES:
        raise ValueError("Invalid staging KYC outcome")
    existing = await db.scalar(
        select(StagingCreatorKycSandboxEvent)
        .where(StagingCreatorKycSandboxEvent.creator_verification_id == verification.id)
        .with_for_update()
    )
    if existing:
        return existing
    event = StagingCreatorKycSandboxEvent(
        creator_verification_id=verification.id,
        external_event_id=f"stg_kyc_evt_{secrets.token_urlsafe(18)}",
        outcome=outcome,
        deliver_after=datetime.now(UTC),
    )
    db.add(event)
    return event


async def process_staging_kyc_webhook(
    db: AsyncSession, payload: bytes, signature: str | None
) -> CreatorVerification | None:
    expected = _staging_kyc_signature(payload)
    if not signature or not hmac.compare_digest(expected, signature):
        raise ValueError("Invalid creator KYC webhook signature")
    try:
        event = json.loads(payload)
        if (
            not isinstance(event, dict)
            or not all(
                isinstance(event.get(key), str) for key in ("id", "type", "provider_reference")
            )
            or any(len(event[key]) > 255 for key in ("id", "type", "provider_reference"))
        ):
            raise ValueError
    except (ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid creator KYC webhook payload") from exc
    existing = await db.scalar(
        select(CreatorKycWebhookEvent)
        .where(
            CreatorKycWebhookEvent.provider == "staging_sandbox",
            CreatorKycWebhookEvent.external_event_id == event["id"],
        )
        .with_for_update()
    )
    if existing:
        return None
    webhook = CreatorKycWebhookEvent(
        provider="staging_sandbox", external_event_id=event["id"], event_type=event["type"]
    )
    db.add(webhook)
    verification = await db.scalar(
        select(CreatorVerification)
        .where(
            CreatorVerification.provider == "staging_sandbox",
            CreatorVerification.provider_reference == event["provider_reference"],
        )
        .with_for_update()
    )
    if not verification:
        webhook.processed_at = datetime.now(UTC)
        return None
    webhook.creator_verification_id = verification.id
    outcome = next(
        (value for value in _STAGING_KYC_OUTCOMES.values() if value[0] == event["type"]), None
    )
    if outcome is None or verification.status is not VerificationStatus.pending:
        webhook.processed_at = datetime.now(UTC)
        return verification
    _event_type, status = outcome
    now = datetime.now(UTC)
    verification.status = status
    verification.identity_verified = status is VerificationStatus.verified
    verification.adult_verified = status is VerificationStatus.verified
    verification.verified_at = now if status is VerificationStatus.verified else None
    verification.expires_at = (
        now + timedelta(days=get_settings().manual_age_review_max_days)
        if status is VerificationStatus.verified
        else None
    )
    verification.failure_reason_code = (
        None
        if status is VerificationStatus.verified
        else "MANUAL_REVIEW_REQUIRED"
        if status is VerificationStatus.needs_review
        else status.value.upper()
    )
    profile = await db.get(CreatorProfile, verification.creator_profile_id, with_for_update=True)
    if (
        profile
        and status is VerificationStatus.verified
        and profile.status is CreatorStatus.pending_verification
    ):
        await set_status(db, profile, CreatorStatus.pending_review, profile.user_id)
    if profile:
        notification = {
            VerificationStatus.verified: (
                "CREATOR_KYC_VERIFIED",
                "Creator identity verification complete",
                "Your verification is complete and your application is ready for review.",
            ),
            VerificationStatus.failed: (
                "CREATOR_KYC_ACTION_REQUIRED",
                "Creator identity verification needs attention",
                "Your verification could not be completed. Start a new verification session.",
            ),
            VerificationStatus.needs_review: (
                "CREATOR_KYC_REVIEW_REQUIRED",
                "Creator identity verification needs review",
                "Your verification requires a manual review.",
            ),
            VerificationStatus.expired: (
                "CREATOR_KYC_REVERIFY_REQUIRED",
                "Creator identity verification expired",
                "Start a new verification session to continue.",
            ),
        }[status]
        await emit_transactional(
            db,
            recipient_user_id=profile.user_id,
            notification_type=notification[0],
            source_domain="creator_kyc",
            source_id=str(verification.id),
            title=notification[1],
            body=notification[2],
            target_path="/creator-studio",
        )
    await record_event(
        db,
        "creator.verification_changed",
        actor_user_id=None,
        target_type="creator_verification",
        target_id=str(verification.id),
        metadata={"provider": "staging_sandbox", "status": status.value},
    )
    webhook.processed_at = now
    return verification


async def deliver_due_staging_kyc_events(db: AsyncSession, limit: int = 50) -> int:
    rows = (
        await db.scalars(
            select(StagingCreatorKycSandboxEvent)
            .where(
                StagingCreatorKycSandboxEvent.delivered_at.is_(None),
                StagingCreatorKycSandboxEvent.deliver_after <= datetime.now(UTC),
            )
            .order_by(StagingCreatorKycSandboxEvent.deliver_after, StagingCreatorKycSandboxEvent.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    delivered = 0
    for event in rows:
        verification = await db.get(
            CreatorVerification, event.creator_verification_id, with_for_update=True
        )
        if not verification:
            event.delivered_at = datetime.now(UTC)
            continue
        event_type, _status = _STAGING_KYC_OUTCOMES[event.outcome]
        payload = json.dumps(
            {
                "id": event.external_event_id,
                "type": event_type,
                "provider_reference": verification.provider_reference,
            },
            separators=(",", ":"),
        ).encode()
        await process_staging_kyc_webhook(db, payload, _staging_kyc_signature(payload))
        event.delivered_at = datetime.now(UTC)
        delivered += 1
    return delivered
