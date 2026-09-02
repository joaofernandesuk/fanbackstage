"""Replay-safe construction of the local demonstration dataset.

Domain services own every state transition and every value-moving operation.
The few direct rows below are seed-only catalogue/configuration adapters for
concepts that intentionally have no write service (profile catalogue links,
pre-rendered media derivatives, reactions, and comments).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.accounts import adult_access
from app.accounts import service as accounts
from app.audit.service import record_event
from app.compliance import policy as compliance_policy
from app.compliance.types import PolicyOverrides, PolicyRules
from app.content import service as content_service
from app.creators import service as creators
from app.featuring import service as featuring
from app.finance import service as finance
from app.groups import service as groups
from app.legal import service as legal_service
from app.marketplace import service as marketplace
from app.media.contexts import require_media_context_available
from app.messaging import service as messaging
from app.models.compliance import (
    AgeAssuranceLevel,
    AgeVerificationRecord,
    AgeVerificationStatus,
    CompliancePolicyStatus,
    CompliancePolicyTemplate,
    CompliancePolicyTemplateRevision,
    CountryRegistry,
    JurisdictionPolicyRevision,
    PerformerAgeVerification,
    PerformerIdentity,
    PerformerIdentityStatus,
    PerformerIdentityVerification,
)
from app.models.content import (
    AccessPolicy,
    ContentItem,
    ContentStatus,
    Gallery,
    MediaAsset,
    MediaAudience,
)
from app.models.creator import (
    CreatorCategory,
    CreatorLanguage,
    CreatorProfile,
    CreatorSocialLink,
    CreatorStatus,
    CreatorVerification,
    VerificationStatus,
)
from app.models.featuring import (
    FeatureBooking,
    FeatureBookingStatus,
    FeaturePrice,
    FeatureSlot,
    FeatureSurfaceKind,
    FeatureTargetType,
)
from app.models.finance import PaymentAttempt, PaymentStatus
from app.models.groups import (
    Group,
    GroupCreatorMembership,
    GroupMembershipStatus,
    GroupPermission,
)
from app.models.identity import User
from app.models.legal import (
    LegalAudience,
    LegalDocument,
    LegalDocumentStatus,
    LegalDocumentType,
    LegalDocumentVersion,
    SiteSettingsVersion,
)
from app.models.marketplace import (
    MarketplaceEarningsHoldPolicy,
    MarketplaceListing,
    MarketplaceListingMedia,
    MarketplaceListingStatus,
    MarketplaceOrderStatus,
    MarketplaceSellerTier,
    MarketplaceShippingAllowance,
    ShippingAllowanceScope,
)
from app.models.messaging import Conversation, Message
from app.models.notification import NotificationPreference
from app.models.referral import (
    ReferralActorType,
    ReferralCommissionPolicy,
    ReferralLink,
    ReferralPolicyStatus,
    ReferralProgram,
    ReferralProgramType,
    SignupAttribution,
)
from app.models.social import (
    FeedPost,
    FeedPostMedia,
    FeedPostStatus,
    PostComment,
    PostReaction,
    ReactionType,
)
from app.models.story import Story, StoryStatus
from app.models.streaming import LiveAccessMode, LiveRoom
from app.models.subscription import SubscriptionPeriod
from app.models.trust_safety import ConsentRelease, ConsentReleaseStatus, ConsentReleaseType
from app.notifications import service as notifications
from app.performers import service as performers
from app.referrals import service as referrals
from app.seed.manifest import (
    CORE_USERS,
    CREATORS,
    FAN_USERS,
    GALLERY_SHOWCASES,
    GROUPS,
    PASSWORD,
    PUBLIC_CREATORS,
    RESTRICTED_CREATOR,
    STORY_CREATORS,
    USERS,
    CreatorSeed,
    gallery_title,
    listing_count_for_creator,
    listing_title,
    post_body,
    story_caption,
    story_cohort_idempotency_key,
    video_title,
)
from app.seed.media import (
    ASSET_ROOT,
    VIDEO_PREVIEW_DURATION_SECONDS,
    ensure_image_asset,
    ensure_video_asset,
    restore_video_preview_ready,
)
from app.social import service as social
from app.stories import service as stories
from app.streaming import service as streaming
from app.subscriptions import service as subscriptions
from app.trust_safety import service as trust_safety

LEGACY_CREATOR_CATEGORY_SLUGS = {
    "collaboration",
    "design",
    "editorial",
    "fashion",
    "lifestyle",
    "live",
    "marketplace",
    "performance",
    "photography",
    "studio",
}


@dataclass(frozen=True)
class SeedStats:
    users: int
    creators: int
    posts: int
    content_items: int
    listings: int
    active_stories: int


@dataclass(frozen=True)
class CreatorContent:
    gallery: ContentItem
    video: ContentItem
    image_asset: MediaAsset
    feed_image_asset: MediaAsset
    video_asset: MediaAsset


def _email(local_part: str) -> str:
    return f"{local_part}@demo.fanbackstage.local"


DEMO_POLICY_KEY = "development-demo-baseline"
DEMO_LEGAL_TYPES = (
    LegalDocumentType.terms,
    LegalDocumentType.privacy,
    LegalDocumentType.age_policy,
)


def _demo_policy_rules() -> PolicyRules:
    """Operational demo rules, explicitly not a statement of applicable law."""

    return PolicyRules(
        enabled=True,
        registration_allowed=True,
        creator_registration_allowed=True,
        purchases_allowed=True,
        subscriptions_allowed=True,
        ppv_allowed=True,
        live_allowed=True,
        marketplace_allowed=True,
        featuring_allowed=True,
        marketing_email_allowed=True,
        messaging_allowed=True,
        minimum_age=18,
        fan_age_verification_required=True,
        anonymous_adult_preview_allowed=True,
        required_assurance_level=AgeAssuranceLevel.self_attested,
        reverify_after_days=30,
        grace_period_days=0,
        creator_identity_required=True,
        creator_age_verification_required=True,
        payout_kyc_required=False,
        co_performer_verification_required=True,
        release_required=True,
        explicit_public_preview_allowed=True,
        restricted_media_policy="development-demo-restricted",
        age_provider="test",
        provider_policy_key=None,
    )


async def _ensure_compliance_demo_policy(
    db: AsyncSession, admin: User
) -> JurisdictionPolicyRevision:
    """Seed three fictional scenarios without asserting real country requirements."""

    now = datetime.now(UTC)
    template = await db.scalar(
        select(CompliancePolicyTemplate).where(CompliancePolicyTemplate.key == DEMO_POLICY_KEY)
    )
    if template is None:
        template = await compliance_policy.create_policy_template(
            db,
            key=DEMO_POLICY_KEY,
            name="Development demo baseline",
            description="Fictional operational scenarios only; legal review is still required.",
            actor_user_id=admin.id,
            change_reason="Create development-only compliance demonstration",
        )
    template_revision = await db.scalar(
        select(CompliancePolicyTemplateRevision)
        .where(CompliancePolicyTemplateRevision.template_id == template.id)
        .order_by(CompliancePolicyTemplateRevision.version.desc())
        .limit(1)
    )
    if template_revision is None:
        template_revision = await compliance_policy.create_template_revision(
            db,
            template_id=template.id,
            rules=_demo_policy_rules(),
            status=CompliancePolicyStatus.active,
            effective_from=now - timedelta(days=1),
            effective_until=None,
            actor_user_id=admin.id,
            reviewed_at=now,
            reviewed_by_user_id=admin.id,
            change_reason="Reviewed fictional development baseline",
            is_demo=True,
        )
    scenarios = {
        "PT": PolicyOverrides(),
        "GB": PolicyOverrides(
            required_assurance_level=AgeAssuranceLevel.medium,
            reverify_after_days=7,
        ),
        "US": PolicyOverrides(
            marketplace_allowed=False,
            purchases_allowed=False,
        ),
    }
    result: JurisdictionPolicyRevision | None = None
    for country_code, overrides in scenarios.items():
        revision = await db.scalar(
            select(JurisdictionPolicyRevision)
            .where(JurisdictionPolicyRevision.country_code == country_code)
            .order_by(JurisdictionPolicyRevision.version.desc())
            .limit(1)
        )
        if revision is None:
            revision = await compliance_policy.create_jurisdiction_revision(
                db,
                country_code=country_code,
                template_revision_id=template_revision.id,
                overrides=overrides,
                status=CompliancePolicyStatus.active,
                effective_from=now - timedelta(days=1),
                effective_until=None,
                actor_user_id=admin.id,
                reviewed_at=now,
                reviewed_by_user_id=admin.id,
                change_reason=(
                    f"Reviewed fictional {country_code} demonstration; not legal guidance"
                ),
                is_demo=True,
            )
        registry = await db.get(CountryRegistry, country_code)
        if registry is not None and not registry.enabled:
            await compliance_policy.set_country_enabled(
                db,
                code=country_code,
                enabled=True,
                actor_user_id=admin.id,
                change_reason="Enable fictional development compliance scenario",
            )
        if country_code == "PT":
            result = revision
    if result is None:
        raise RuntimeError("The PT development compliance scenario is missing")
    return result


async def _ensure_demo_age_verifications(
    db: AsyncSession,
    users: dict[str, User],
    policy: JurisdictionPolicyRevision,
) -> None:
    """Create provider-normalized verified, expired, failed, and absent fan states."""

    now = datetime.now(UTC)
    fixtures = (
        (_email("subscriber"), "verified", AgeVerificationStatus.verified),
        (_email("socialfan"), "expired", AgeVerificationStatus.expired),
        (_email("marketing-out"), "failed", AgeVerificationStatus.failed),
    )
    for email, key, status in fixtures:
        reference = f"demo-{key}-fan-v1"
        existing = await db.scalar(
            select(AgeVerificationRecord.id).where(
                AgeVerificationRecord.provider == "test",
                AgeVerificationRecord.provider_verification_id == reference,
            )
        )
        if existing:
            # A deterministic demo identity can survive a long-lived local
            # database. Refresh only the intentionally-current fixture so
            # re-running the seed remains convergent rather than inheriting a
            # stale verified result from an earlier local run.
            row = await db.get(AgeVerificationRecord, existing)
            if (
                status is AgeVerificationStatus.verified
                and row is not None
                and (row.expires_at is None or row.expires_at <= now)
            ):
                row.verified_at = now - timedelta(days=1)
                row.expires_at = now + timedelta(days=29)
            continue
        verified_at = (
            now - timedelta(days=45)
            if status is AgeVerificationStatus.expired
            else now - timedelta(days=1)
            if status is AgeVerificationStatus.verified
            else None
        )
        row = AgeVerificationRecord(
            user_id=users[email].id,
            provider="test",
            provider_verification_id=reference,
            state_hash=sha256(f"fanbackstage:{reference}".encode()).hexdigest(),
            state_consumed_at=now,
            safe_return_path="/account",
            country_code="PT",
            applicable_policy_id=policy.id,
            applicable_policy_version=policy.version,
            required_minimum_age=18,
            achieved_minimum_age=(18 if status is not AgeVerificationStatus.failed else None),
            required_assurance_level=AgeAssuranceLevel.self_attested,
            achieved_assurance_level=(
                AgeAssuranceLevel.medium
                if status is not AgeVerificationStatus.failed
                else AgeAssuranceLevel.none
            ),
            status=status,
            initiated_at=(verified_at or now - timedelta(days=2)),
            verified_at=verified_at,
            failed_at=now - timedelta(days=2) if status is AgeVerificationStatus.failed else None,
            expires_at=(
                now + timedelta(days=29)
                if status is AgeVerificationStatus.verified
                else now - timedelta(days=15)
                if status is AgeVerificationStatus.expired
                else None
            ),
            failure_reason_code=(
                "DEMO_AGE_NOT_VERIFIED" if status is AgeVerificationStatus.failed else None
            ),
            retryable=status is AgeVerificationStatus.failed,
            result_metadata_json={"demo_fixture": True},
        )
        db.add(row)
        await db.flush()
        await record_event(
            db,
            "compliance.demo_age_verification_seeded",
            actor_user_id=users[email].id,
            target_type="age_verification_record",
            target_id=str(row.id),
            metadata={"status": status.value, "country_code": "PT"},
        )


async def _ensure_demo_legal_content(
    db: AsyncSession, admin: User, users: dict[str, User]
) -> list[LegalDocumentVersion]:
    """Seed required demo pages and review-required drafts for every other type."""

    published: list[LegalDocumentVersion] = []
    for document_type in LegalDocumentType:
        slug = document_type.value.replace("_", "-")
        document = await db.scalar(
            select(LegalDocument).where(
                LegalDocument.slug == slug,
                LegalDocument.jurisdiction_code.is_(None),
                LegalDocument.language == "en",
                LegalDocument.audience == LegalAudience.all_users,
            )
        )
        if document is None:
            document, version = await legal_service.create_document(
                db,
                admin,
                {
                    "document_type": document_type,
                    "slug": slug,
                    "jurisdiction_code": None,
                    "language": "en",
                    "audience": LegalAudience.all_users,
                    "title": f"Development placeholder: {document_type.value.replace('_', ' ')}",
                    "body": [
                        {
                            "type": "callout",
                            "text": (
                                "Development-only placeholder. This is not approved legal text, "
                                "legal advice, or a claim of compliance."
                            ),
                        }
                    ],
                    "requires_acceptance": document_type in DEMO_LEGAL_TYPES,
                    "requires_legal_review": document_type not in DEMO_LEGAL_TYPES,
                    "approved_for_publication": document_type in DEMO_LEGAL_TYPES,
                    "is_demo": True,
                },
            )
            if document_type in DEMO_LEGAL_TYPES:
                await legal_service.publish_version(
                    db,
                    admin,
                    version.id,
                    reason="Activate explicit development-only placeholder",
                )
        version = await db.scalar(
            select(LegalDocumentVersion)
            .where(
                LegalDocumentVersion.document_id == document.id,
                LegalDocumentVersion.is_demo.is_(True),
            )
            .order_by(LegalDocumentVersion.version.desc())
            .limit(1)
        )
        if (
            version is not None
            and document_type in DEMO_LEGAL_TYPES
            and version.status is LegalDocumentStatus.published
        ):
            published.append(version)
    required_ids = [version.id for version in published]
    for user in users.values():
        await legal_service.record_acceptances(
            db,
            user,
            required_ids,
            source="account",
            jurisdiction_code="PT",
            correlation_id=None,
        )
    current_settings = await db.scalar(
        select(SiteSettingsVersion).where(SiteSettingsVersion.is_current.is_(True))
    )
    if current_settings is None:
        await legal_service.update_site_settings(
            db,
            admin,
            {
                "support_email": "support@demo.fanbackstage.local",
                "footer_text": "Development-only FanBackstage compliance demonstration.",
                "public_contact_text": "Fictional local support contact for manual QA.",
                "social_links": [],
                "homepage_announcement": (
                    "Development demo: country policies and legal copy require real review."
                ),
                "maintenance_notice": None,
                "banner_level": "info",
                "banner_starts_at": None,
                "banner_ends_at": None,
                "reason": "Create development-only public site settings",
            },
        )
    return published


async def _ensure_user(db: AsyncSession, email: str, role_names: tuple[str, ...]) -> User:
    user = await db.scalar(select(User).where(User.email == email))
    if not user:
        user, _ = await accounts.register(
            db,
            email,
            PASSWORD,
            None,
            adult_confirmed=True,
            country_code="PT",
        )
    user.country_code = "PT"
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
    adult_access.attest_account(user)
    for role_name in role_names:
        if role_name not in {role.name for role in user.roles}:
            await accounts.assign_role(db, user, role_name, user.id, None)
    return user


async def _ensure_creator_catalogue(
    db: AsyncSession, profile: CreatorProfile, seed: CreatorSeed
) -> None:
    category = await db.scalar(select(CreatorCategory).where(CreatorCategory.slug == seed.category))
    if not category:
        category = CreatorCategory(
            slug=seed.category,
            label=seed.category.replace("-", " ").title(),
            position=len(profile.categories),
        )
        db.add(category)
        await db.flush()
    # This runs only for immutable fictional demo profiles. Retire their old
    # generic categories so repeated local seeding converges on the current
    # discovery-interest catalogue without touching real creator selections.
    profile.categories[:] = [
        existing
        for existing in profile.categories
        if existing.slug not in LEGACY_CREATOR_CATEGORY_SLUGS
    ]
    if category not in profile.categories:
        profile.categories.append(category)
    language = await db.scalar(
        select(CreatorLanguage).where(CreatorLanguage.code == seed.language_code)
    )
    if not language:
        language = CreatorLanguage(code=seed.language_code, label=seed.language_label)
        db.add(language)
        await db.flush()
    if language not in profile.languages:
        profile.languages.append(language)
    link_url = f"https://demo.fanbackstage.local/{seed.slug}"
    if not any(link.url == link_url for link in profile.links):
        profile.links.append(CreatorSocialLink(label="Demo portfolio", url=link_url, position=0))


async def _ensure_creator(
    db: AsyncSession, admin: User, user: User, seed: CreatorSeed
) -> CreatorProfile:
    profile = await creators.get_or_create_profile(db, user)
    await db.refresh(profile, ["categories", "languages", "links"])
    await creators.update_profile(
        db,
        profile,
        {
            "username": seed.slug,
            "display_name": seed.display_name,
            "bio": seed.bio,
            "country_code": "PT",
            "region": seed.city,
            "city": seed.city,
            "show_location": True,
            "timezone": "Europe/Lisbon",
        },
        user.id,
    )
    profile.avatar_reference = seed.avatar_reference
    profile.cover_reference = seed.cover_reference
    await _ensure_creator_catalogue(db, profile, seed)
    if profile.status is CreatorStatus.draft:
        await creators.submit(db, profile, user.id)
    if profile.status is CreatorStatus.pending_verification:
        verified = await db.scalar(
            select(CreatorVerification).where(
                CreatorVerification.creator_profile_id == profile.id,
                CreatorVerification.status == VerificationStatus.verified,
                CreatorVerification.adult_verified.is_(True),
            )
        )
        if verified:
            await creators.set_status(db, profile, CreatorStatus.pending_review, admin.id)
        else:
            await creators.development_verify(db, profile, True, admin.id)
    if profile.status is CreatorStatus.pending_review:
        await creators.set_status(db, profile, CreatorStatus.approved, admin.id)
    if seed == RESTRICTED_CREATOR:
        if profile.status is CreatorStatus.approved:
            await creators.set_status(
                db,
                profile,
                CreatorStatus.suspended,
                admin.id,
                "Fictional demo profile retained for a safety review workflow",
            )
        profile.is_public = False
    elif profile.status is CreatorStatus.approved:
        await _ensure_current_demo_creator_verification(db, admin, profile, seed)
        await creators.update_profile(db, profile, {"is_public": True}, user.id)
    else:
        raise RuntimeError(
            f"Demo creator {seed.slug} is {profile.status.value}; "
            "the seed will not override an existing moderation decision"
        )
    return profile


async def _ensure_current_demo_creator_verification(
    db: AsyncSession,
    admin: User,
    profile: CreatorProfile,
    seed: CreatorSeed,
) -> None:
    """Converge the explicit development KYC fixture for public demo creators.

    The demo policy deliberately requires current creator identity and adult
    verification.  A long-lived local database can otherwise retain an old
    development verification after its 30-day re-verification window, which
    correctly blocks media writes but makes a repeatable demo seed unusable.
    This stable, development-only fixture is refreshed in place so reseeding
    repairs local demo data without changing the production verification flow.
    """

    eligibility = await creators.resolve_creator_compliance_eligibility(db, profile=profile)
    if eligibility.public_allowed:
        return

    now = datetime.now(UTC)
    provider_reference = f"demo-current-creator-kyc-{seed.slug}-v1"
    verification = await db.scalar(
        select(CreatorVerification).where(
            CreatorVerification.provider_reference == provider_reference
        )
    )
    if verification is None:
        verification = CreatorVerification(
            creator_profile_id=profile.id,
            provider="development",
            provider_reference=provider_reference,
            status=VerificationStatus.verified,
            adult_verified=True,
            identity_verified=True,
            country_code="PT",
            verified_at=now,
            expires_at=now + timedelta(days=30),
            metadata_json={"demo_fixture": True, "purpose": "current_creator_compliance"},
        )
        db.add(verification)
        await db.flush()
        await record_event(
            db,
            "creator.demo_current_kyc_seeded",
            actor_user_id=admin.id,
            target_type="creator_verification",
            target_id=str(verification.id),
            metadata={"creator_profile_id": str(profile.id), "status": "verified"},
        )
    else:
        verification.creator_profile_id = profile.id
        verification.status = VerificationStatus.verified
        verification.adult_verified = True
        verification.identity_verified = True
        verification.country_code = "PT"
        verification.verified_at = now
        verification.expires_at = now + timedelta(days=30)
        verification.revoked_at = None
        verification.failure_reason_code = None
        verification.metadata_json = {
            "demo_fixture": True,
            "purpose": "current_creator_compliance",
        }
        await db.flush()

    refreshed = await creators.resolve_creator_compliance_eligibility(db, profile=profile)
    if not refreshed.public_allowed:
        raise RuntimeError(
            f"Demo creator {seed.slug} remains ineligible after its current development "
            f"KYC fixture was seeded: {refreshed.code}"
        )


async def _ensure_pending_creator_kyc(
    db: AsyncSession, admin: User, profile: CreatorProfile
) -> None:
    """Keep the suspended demo creator as the explicit pending-KYC support case."""

    provider_reference = "demo-pending-creator-kyc-reya-v1"
    if await db.scalar(
        select(CreatorVerification.id).where(
            CreatorVerification.provider_reference == provider_reference
        )
    ):
        return
    verification = CreatorVerification(
        creator_profile_id=profile.id,
        provider="development",
        provider_reference=provider_reference,
        status=VerificationStatus.pending,
        adult_verified=False,
        identity_verified=False,
        country_code="PT",
        metadata_json={"demo_fixture": True, "purpose": "pending_kyc_manual_qa"},
    )
    db.add(verification)
    await db.flush()
    await record_event(
        db,
        "creator.demo_pending_kyc_seeded",
        actor_user_id=admin.id,
        target_type="creator_verification",
        target_id=str(verification.id),
        metadata={"creator_profile_id": str(profile.id), "status": "pending"},
    )


async def _ensure_marketing_preferences(db: AsyncSession, users: dict[str, User]) -> None:
    """Converge the marketing QA personas through the notification domain service."""

    fixtures = (
        (users[_email("marketing-in")], True, True, True),
        (users[_email("marketing-out")], False, True, False),
    )
    for user, email_enabled, in_app_enabled, consent in fixtures:
        preference = await db.scalar(
            select(NotificationPreference).where(
                NotificationPreference.user_id == user.id,
                NotificationPreference.category == "marketing",
            )
        )
        is_converged = bool(
            preference
            and preference.email_enabled is email_enabled
            and preference.in_app_enabled is in_app_enabled
            and (
                (
                    preference.consented_at is not None
                    and preference.consent_source == "account_settings"
                )
                if consent
                else preference.consented_at is None and preference.consent_source is None
            )
        )
        if not is_converged:
            await notifications.update_preference(
                db,
                user,
                "marketing",
                email_enabled,
                in_app_enabled,
                consent=consent,
            )


async def _ensure_referral(db: AsyncSession, users: dict[str, User]) -> None:
    owner = users[_email("marketing-in")]
    attributed_user = users[_email("ppvbuyer")]
    program = await db.scalar(
        select(ReferralProgram).where(
            ReferralProgram.actor_type == ReferralActorType.user,
            ReferralProgram.program_type == ReferralProgramType.user_user_referral,
            ReferralProgram.owner_user_id == owner.id,
        )
    )
    if not program:
        program = await referrals.create_program(
            db,
            actor_type=ReferralActorType.user,
            program_type=ReferralProgramType.user_user_referral,
            owner_user_id=owner.id,
            terms_reference="demo://local-referral-terms-v1",
        )
    policy = await db.scalar(
        select(ReferralCommissionPolicy)
        .where(
            ReferralCommissionPolicy.program_id == program.id,
            ReferralCommissionPolicy.status == ReferralPolicyStatus.active,
        )
        .order_by(ReferralCommissionPolicy.version.desc())
    )
    if not policy:
        policy = await referrals.create_policy(
            db,
            program,
            basis_points=1_000,
            eligible_revenue_types=["marketplace", "ppv", "subscription"],
        )
    link = await db.scalar(select(ReferralLink).where(ReferralLink.code == "DEMO-FANBACKSTAGE"))
    if not link:
        link = await referrals.create_link(
            db,
            program,
            policy,
            code="DEMO-FANBACKSTAGE",
            destination_path="/discover",
            source="local-demo",
        )
    attribution = await db.scalar(
        select(SignupAttribution).where(SignupAttribution.user_id == attributed_user.id)
    )
    if not attribution:
        _, token = await referrals.resolve_click(
            db,
            link.code,
            "fanbackstage-deterministic-demo-referral-session",
            source="local-demo",
            utm={"source": "demo", "campaign": "local-rebuild"},
        )
        await referrals.snapshot_signup_attribution(db, attributed_user, token)


async def _ensure_groups(
    db: AsyncSession,
    users: dict[str, User],
    profiles: dict[str, CreatorProfile],
) -> None:
    manager = users[_email("manager")]
    permissions = [
        GroupPermission.manage_content,
        GroupPermission.publish_posts,
        GroupPermission.view_analytics,
        GroupPermission.view_earnings,
        GroupPermission.manage_marketplace,
        GroupPermission.manage_marketplace_orders,
        GroupPermission.manage_featuring,
    ]
    for slug, name, creator_slug, creator_bps in GROUPS:
        group = await db.scalar(select(Group).where(Group.slug == slug))
        if not group:
            group = await groups.create_group(
                db,
                manager,
                name,
                slug,
                creator_bps,
                "Fictional development-only creator management agency.",
            )
        profile = profiles[creator_slug]
        membership = await db.scalar(
            select(GroupCreatorMembership).where(
                GroupCreatorMembership.group_id == group.id,
                GroupCreatorMembership.creator_id == profile.id,
                GroupCreatorMembership.status.in_(
                    [
                        GroupMembershipStatus.invited,
                        GroupMembershipStatus.pending_acceptance,
                        GroupMembershipStatus.active,
                        GroupMembershipStatus.leaving,
                    ]
                ),
            )
        )
        if not membership:
            membership = await groups.invite_creator(
                db,
                group.id,
                manager,
                profile.id,
                creator_bps,
                permissions,
            )
        if membership.status is GroupMembershipStatus.invited:
            await groups.accept_invitation(
                db, membership.id, users[f"{creator_slug}@demo.fanbackstage.local"]
            )


def _gallery_policy(position: int) -> AccessPolicy:
    return (
        AccessPolicy.free,
        AccessPolicy.followers,
        AccessPolicy.subscription,
    )[position % 3]


def _video_policy(position: int, creator: CreatorSeed) -> AccessPolicy:
    if position % 2 == 0 or creator.slug == "aria-group":
        return AccessPolicy.ppv
    return AccessPolicy.subscription if position % 3 == 1 else AccessPolicy.free


async def _content_by_title(db: AsyncSession, creator_id, title: str) -> ContentItem | None:
    return await db.scalar(
        select(ContentItem)
        .options(
            selectinload(ContentItem.gallery).selectinload(Gallery.items),
            selectinload(ContentItem.video),
        )
        .where(ContentItem.owner_creator_id == creator_id, ContentItem.title == title)
    )


async def _ensure_creator_content(
    db: AsyncSession,
    admin: User,
    creator_user: User,
    profile: CreatorProfile,
    seed: CreatorSeed,
    position: int,
    provider,
    asset_root: Path,
) -> CreatorContent:
    image_asset = await ensure_image_asset(
        db,
        creator_user,
        profile,
        seed.slug,
        provider,
        asset_root,
        classification_actor=admin,
    )
    # Asset contexts are exclusive: the gallery master cannot also back a
    # free feed post. Keep the deterministic feed illustration distinct while
    # retaining idempotent filename-based seed convergence.
    feed_image_asset = await ensure_image_asset(
        db,
        creator_user,
        profile,
        seed.slug,
        provider,
        asset_root,
        variant="feed",
        classification_actor=admin,
    )
    video_asset = await ensure_video_asset(
        db,
        creator_user,
        profile,
        seed.slug,
        provider,
        asset_root,
        audience=(
            MediaAudience.adult_restricted
            if seed.slug == "zara-pulse"
            else MediaAudience.safe_public
        ),
        classification_actor=admin,
    )
    feed_settings = await social.settings_for_creator(db, profile.id)
    feed_settings.auto_post_galleries = False
    feed_settings.auto_post_videos = False

    gallery = await _content_by_title(db, profile.id, gallery_title(seed))
    gallery_policy = _gallery_policy(position)
    if not gallery:
        gallery = await content_service.create_gallery(
            db,
            creator_user,
            gallery_title(seed),
            "A fictional, harmless collection produced for local product testing.",
            gallery_policy,
        )
    assert gallery.gallery
    await db.refresh(gallery.gallery, ["items"])
    if not gallery.gallery.items:
        await content_service.add_gallery_item(
            db,
            creator_user,
            gallery.id,
            image_asset.id,
            preview=gallery_policy is not AccessPolicy.free,
        )
        if gallery_policy is not AccessPolicy.free:
            await content_service.configure_gallery_preview(
                db, creator_user, gallery.id, 1, {image_asset.id}
            )
    if gallery.status is ContentStatus.processing:
        await content_service.submit_for_review(db, creator_user, gallery.id)
    if gallery.status is ContentStatus.pending_review:
        await content_service.approve(db, gallery, admin)

    video = await _content_by_title(db, profile.id, video_title(seed))
    video_policy = _video_policy(position, seed)
    if not video:
        video = await content_service.create_video(
            db,
            creator_user,
            video_title(seed),
            "An eight-second fictional studio reel with a distinct two-second acquisition trailer.",
            video_asset.id,
            video_policy,
            preview_start_seconds=0,
            preview_duration_seconds=VIDEO_PREVIEW_DURATION_SECONDS,
            price_amount_minor=599 + position * 25 if video_policy is AccessPolicy.ppv else None,
            price_currency="EUR" if video_policy is AccessPolicy.ppv else None,
        )
        await restore_video_preview_ready(db, video_asset.id)
    assert video.video
    video.description = (
        "An eight-second fictional studio reel with a distinct two-second acquisition trailer."
    )
    video.video.preview_start_seconds = 0
    video.video.preview_duration_seconds = VIDEO_PREVIEW_DURATION_SECONDS
    if video.status is ContentStatus.processing:
        await restore_video_preview_ready(db, video_asset.id)
        await content_service.submit_for_review(db, creator_user, video.id)
    if video.status is ContentStatus.pending_review:
        await content_service.approve(db, video, admin)
    if gallery.status is not ContentStatus.published or video.status is not ContentStatus.published:
        raise RuntimeError(f"Demo content did not publish for {seed.slug}")
    return CreatorContent(gallery, video, image_asset, feed_image_asset, video_asset)


async def _ensure_showcase_galleries(
    db: AsyncSession,
    admin: User,
    users: dict[str, User],
    profiles: dict[str, CreatorProfile],
    provider,
    asset_root: Path,
) -> None:
    """Create real multi-image free, subscriber, and PPV gallery fixtures."""
    for showcase in GALLERY_SHOWCASES:
        creator = profiles[showcase.creator_slug]
        creator_user = users[_email(showcase.creator_slug)]
        assets = [
            await ensure_image_asset(
                db,
                creator_user,
                creator,
                showcase.creator_slug,
                provider,
                asset_root,
                variant=f"gallery-showcase-{position}",
                audience=(
                    MediaAudience.adult_restricted
                    if showcase.creator_slug == "zara-pulse" and position > 1
                    else MediaAudience.safe_public
                ),
                classification_actor=admin,
            )
            for position in range(1, 5)
        ]
        gallery = await _content_by_title(db, creator.id, showcase.title)
        policy = AccessPolicy(showcase.access_policy)
        if not gallery:
            gallery = await content_service.create_gallery(
                db,
                creator_user,
                showcase.title,
                showcase.description,
                policy,
                showcase.price_amount_minor,
                "EUR" if showcase.price_amount_minor is not None else None,
            )
        # Demo gallery approval must never change the separately asserted feed
        # post manifest, even if creator defaults are changed in a later run.
        gallery.feed_announcement_override = False
        assert gallery.gallery
        await db.refresh(gallery.gallery, ["items"])
        existing_asset_ids = {item.media_asset_id for item in gallery.gallery.items}
        if existing_asset_ids != {asset.id for asset in assets}:
            if gallery.status not in {ContentStatus.draft, ContentStatus.processing}:
                raise RuntimeError(f"Published demo gallery is incomplete: {showcase.title}")
            for position, asset in enumerate(assets):
                if asset.id not in existing_asset_ids:
                    await content_service.add_gallery_item(
                        db,
                        creator_user,
                        gallery.id,
                        asset.id,
                        preview=policy is not AccessPolicy.free and position == 0,
                    )
            await content_service.configure_gallery_cover(
                db, creator_user, gallery.id, assets[0].id
            )
            await content_service.configure_gallery_preview(
                db,
                creator_user,
                gallery.id,
                0 if policy is AccessPolicy.free else 1,
                set() if policy is AccessPolicy.free else {assets[0].id},
            )
        if gallery.status is ContentStatus.processing:
            await content_service.submit_for_review(db, creator_user, gallery.id)
        if gallery.status is ContentStatus.pending_review:
            await content_service.approve(db, gallery, admin)
        if gallery.status is not ContentStatus.published:
            raise RuntimeError(f"Demo gallery did not publish: {showcase.title}")


async def _ensure_performer_consent_example(
    db: AsyncSession,
    admin: User,
    users: dict[str, User],
    profiles: dict[str, CreatorProfile],
    content: dict[str, CreatorContent],
) -> None:
    """Build one private co-performer identity, verification, release, and content link."""

    creator = profiles["zara-pulse"]
    creator_user = users[_email("zara-pulse")]
    target = content["zara-pulse"].video
    safe_reference = "Demo co-performer A"
    performer = await db.scalar(
        select(PerformerIdentity).where(
            PerformerIdentity.owner_creator_id == creator.id,
            PerformerIdentity.safe_reference == safe_reference,
        )
    )
    if performer is None:
        performer = await performers.create_identity(
            db,
            creator_user,
            safe_reference,
            country_code="PT",
        )
    identity_reference = "demo-performer-identity-zara-v1"
    if not await db.scalar(
        select(PerformerIdentityVerification.id).where(
            PerformerIdentityVerification.provider == "development",
            PerformerIdentityVerification.provider_reference == identity_reference,
        )
    ):
        await performers.record_identity_verification(
            db,
            admin,
            performer.id,
            provider="development",
            provider_reference=identity_reference,
            status=PerformerIdentityStatus.verified,
            country_code="PT",
            expires_at=datetime.now(UTC) + timedelta(days=365),
            confirmed=True,
            reason="Reviewed fictional development performer identity fixture",
        )
    age_reference = "demo-performer-age-zara-v1"
    if not await db.scalar(
        select(PerformerAgeVerification.id).where(
            PerformerAgeVerification.provider == "development",
            PerformerAgeVerification.provider_reference == age_reference,
        )
    ):
        await performers.record_age_verification(
            db,
            admin,
            performer.id,
            provider="development",
            provider_reference=age_reference,
            status=AgeVerificationStatus.verified,
            country_code="PT",
            required_minimum_age=18,
            achieved_assurance_level=AgeAssuranceLevel.medium,
            expires_at=datetime.now(UTC) + timedelta(days=365),
            confirmed=True,
            reason="Reviewed fictional development performer age fixture",
        )
    release = await db.scalar(
        select(ConsentRelease)
        .where(
            ConsentRelease.owner_creator_id == creator.id,
            ConsentRelease.participant_reference == safe_reference,
        )
        .order_by(ConsentRelease.created_at.desc())
        .limit(1)
    )
    if release is None:
        release = await trust_safety.submit_consent_release(
            db,
            creator,
            creator_user,
            ConsentReleaseType.co_performer_release,
            safe_reference,
            [target.id],
            effective_until=datetime.now(UTC) + timedelta(days=365),
            evidence_reference="demo://fictional-performer-release-evidence",
        )
    if release.status is ConsentReleaseStatus.pending:
        await trust_safety.verify_consent_release(db, release, admin, approved=True)
    if release.status is not ConsentReleaseStatus.verified:
        raise RuntimeError("The demo co-performer release is not current")
    await performers.link_content_performer(
        db,
        creator_user,
        target.id,
        performer.id,
        release.id,
    )


async def _ensure_posts(
    db: AsyncSession,
    creator_user: User,
    profile: CreatorProfile,
    seed: CreatorSeed,
    bundle: CreatorContent,
) -> list[FeedPost]:
    values = (
        {
            "post_type": "image",
            "body": post_body(seed, 0),
            "media_asset_ids": [bundle.feed_image_asset.id],
        },
        {"post_type": "text", "body": post_body(seed, 1)},
        {
            "post_type": "gallery_reference",
            "body": post_body(seed, 2),
            "content_id": bundle.gallery.id,
        },
        {
            "post_type": "video_reference",
            "body": post_body(seed, 3),
            "content_id": bundle.video.id,
        },
    )
    rows: list[FeedPost] = []
    for item in values:
        post = await db.scalar(
            select(FeedPost).where(
                FeedPost.creator_id == profile.id,
                FeedPost.body == item["body"],
            )
        )
        if not post:
            post = await social.create_post(
                db,
                creator_user,
                {**item, "access_policy": AccessPolicy.free},
            )
        media_asset_ids = item.get("media_asset_ids", [])
        if media_asset_ids:
            await _reconcile_seed_post_media(db, post, media_asset_ids)
        if post.status in {FeedPostStatus.draft, FeedPostStatus.scheduled}:
            await social.publish(db, creator_user, post.id)
        rows.append(post)
    return rows


async def _reconcile_seed_post_media(
    db: AsyncSession, post: FeedPost, media_asset_ids: list[UUID]
) -> None:
    """Repair legacy demo posts to their dedicated feed-only media assets.

    Early demo datasets reused gallery media in feed posts.  That is now
    deliberately rejected by the final media-delivery boundary, so repeatable
    development seeding must migrate those old attachments rather than leave
    visually broken cards behind.
    """

    attachments = list(
        await db.scalars(
            select(FeedPostMedia)
            .where(FeedPostMedia.post_id == post.id)
            .order_by(FeedPostMedia.position)
        )
    )
    if [attachment.media_asset_id for attachment in attachments] == media_asset_ids:
        return
    for attachment in attachments:
        await db.delete(attachment)
    await db.flush()
    for position, asset_id in enumerate(media_asset_ids):
        await require_media_context_available(db, asset_id, context_type="feed", context_id=post.id)
        db.add(
            FeedPostMedia(
                post_id=post.id,
                media_asset_id=asset_id,
                position=position,
            )
        )
    await db.flush()


async def _ensure_stories(
    db: AsyncSession,
    admin: User,
    users: dict[str, User],
    profiles: dict[str, CreatorProfile],
    provider,
    asset_root: Path,
) -> None:
    """Keep a fresh 24-hour Story set while retaining expired demo history."""

    reference = datetime.now(UTC)
    await stories.expire_due_stories(db, now=reference)
    for creator_position, seed in enumerate(STORY_CREATORS):
        creator = profiles[seed.slug]
        creator_user = users[seed.email]
        for position in range(3):
            caption = story_caption(seed, position)
            existing = await db.scalar(
                select(Story.id).where(
                    Story.creator_id == creator.id,
                    Story.caption == caption,
                    Story.status == StoryStatus.active,
                    Story.expires_at > reference,
                )
            )
            if existing:
                continue
            if position < 2 or creator_position % 3 == 2:
                policy = AccessPolicy.free
            elif creator_position % 3 == 0:
                policy = AccessPolicy.followers
            else:
                policy = AccessPolicy.subscription
            if position % 2 == 0:
                story_asset = await ensure_image_asset(
                    db,
                    creator_user,
                    creator,
                    seed.slug,
                    provider,
                    asset_root,
                    variant=f"story-{position}",
                    classification_actor=admin,
                )
            else:
                story_asset = await ensure_video_asset(
                    db,
                    creator_user,
                    creator,
                    seed.slug,
                    provider,
                    asset_root,
                    variant=f"story-{position}",
                    classification_actor=admin,
                )
            await stories.create_story(
                db,
                creator_user,
                story_asset.id,
                caption,
                f"{seed.display_name} demo Story {position + 1}",
                policy,
                story_cohort_idempotency_key(seed, position, reference),
                now=reference - timedelta(minutes=(creator_position * 10) + position + 1),
            )

        if creator_position >= 4:
            continue
        caption = story_caption(seed, creator_position, historical=True)
        historical = await db.scalar(
            select(Story.id).where(
                Story.creator_id == creator.id,
                Story.caption == caption,
            )
        )
        if not historical:
            historical_asset = await ensure_image_asset(
                db,
                creator_user,
                creator,
                seed.slug,
                provider,
                asset_root,
                variant="story-historical",
                classification_actor=admin,
            )
            await stories.create_story(
                db,
                creator_user,
                historical_asset.id,
                caption,
                f"Expired {seed.display_name} demo Story",
                AccessPolicy.free,
                f"demo-story-{seed.slug}-historical-{creator_position + 1}",
                now=reference - timedelta(days=2, minutes=creator_position),
            )
    await stories.expire_due_stories(db, now=reference)


async def _ensure_social_graph(
    db: AsyncSession,
    users: dict[str, User],
    profiles: dict[str, CreatorProfile],
    posts: dict[str, list[FeedPost]],
) -> None:
    fan_seeds = CORE_USERS + FAN_USERS
    public_profiles = [profiles[seed.slug] for seed in PUBLIC_CREATORS]
    for position, fan_seed in enumerate(fan_seeds):
        fan = users[fan_seed.email]
        for offset in (0, 3, 6, 9):
            await social.follow(
                db, fan, public_profiles[(position + offset) % len(public_profiles)].id
            )
    reaction_users = [users[item.email] for item in fan_seeds[:8]]
    comment_users = [users[item.email] for item in fan_seeds[8:12]]
    reaction_types = tuple(ReactionType)
    for creator_position, creator_seed in enumerate(PUBLIC_CREATORS):
        for post_position, post in enumerate(posts[creator_seed.slug][:2]):
            for user_position, user in enumerate(reaction_users):
                if not await social.can_access_post(db, post, user):
                    raise RuntimeError("Demo social engagement targets an inaccessible post")
                reaction = await db.scalar(
                    select(PostReaction).where(
                        PostReaction.post_id == post.id,
                        PostReaction.user_id == user.id,
                    )
                )
                if not reaction:
                    db.add(
                        PostReaction(
                            post_id=post.id,
                            user_id=user.id,
                            reaction_type=reaction_types[
                                (creator_position + post_position + user_position)
                                % len(reaction_types)
                            ],
                        )
                    )
            for user_position, user in enumerate(comment_users):
                body = (
                    "The lighting and color story are lovely."
                    if user_position % 2 == 0
                    else "This is such a welcoming behind-the-scenes update!"
                )
                body = f"{body} [{creator_seed.slug}:{post_position}:{user_position}]"
                comment = await db.scalar(
                    select(PostComment).where(
                        PostComment.post_id == post.id,
                        PostComment.user_id == user.id,
                        PostComment.body == body,
                    )
                )
                if not comment:
                    db.add(PostComment(post_id=post.id, user_id=user.id, body=body))


async def _ensure_message(
    db: AsyncSession, conversation: Conversation, sender: User, body: str
) -> Message:
    existing = await db.scalar(
        select(Message).where(
            Message.conversation_id == conversation.id,
            Message.sender_user_id == sender.id,
            Message.body == body,
        )
    )
    if existing:
        return existing
    return await messaging.send_in_conversation(db, sender, conversation.id, body)


async def _ensure_conversations(
    db: AsyncSession,
    users: dict[str, User],
    profiles: dict[str, CreatorProfile],
) -> None:
    pairs = (
        ("subscriber", "luna-sparks"),
        # ``socialfan`` intentionally demonstrates expired assurance and must
        # not be used to create a fresh protected-message mutation.
        ("subscriber", "mira-nova"),
        ("newfan", "zara-pulse"),
        ("marketing-in", "sera-kim"),
    )
    for fan_local, creator_slug in pairs:
        fan = users[_email(fan_local)]
        creator = profiles[creator_slug]
        creator_user = users[_email(creator_slug)]
        conversation = await db.scalar(
            select(Conversation).where(
                Conversation.creator_id == creator.id,
                Conversation.viewer_user_id == fan.id,
            )
        )
        initial_body = f"Hi {creator.display_name}, I loved the latest studio update."
        if not conversation:
            await messaging.send_message(db, fan, creator.id, initial_body)
            await db.flush()
            conversation = await db.scalar(
                select(Conversation).where(
                    Conversation.creator_id == creator.id,
                    Conversation.viewer_user_id == fan.id,
                )
            )
        assert conversation
        await _ensure_message(db, conversation, fan, initial_body)
        await _ensure_message(
            db,
            conversation,
            creator_user,
            "Thank you! I’m glad you’re here—there’s another demo update coming soon.",
        )
        await _ensure_message(
            db,
            conversation,
            fan,
            "Perfect, I’ll keep an eye on the feed. Thanks for replying!",
        )


async def _ensure_live_history(
    db: AsyncSession, users: dict[str, User], profiles: dict[str, CreatorProfile]
) -> None:
    creator = profiles["skye-live"]
    title = "Demo studio Q&A — safely ended"
    existing = await db.scalar(
        select(LiveRoom).where(LiveRoom.creator_id == creator.id, LiveRoom.title == title)
    )
    if not existing:
        creator_user = users[_email("skye-live")]
        room = await streaming.start_live(
            db,
            creator_user,
            title,
            LiveAccessMode.public,
            "An ended local-only room retained as streaming history.",
        )
        # This is a historical fixture, not a provider-backed room. Finalize
        # its local lifecycle directly so the seed never performs (or queues)
        # a LiveKit control before the seed transaction commits.
        await streaming._mark_public_room_ended(db, room)


async def _ensure_subscription_plans(db: AsyncSession, profiles: dict[str, CreatorProfile]) -> None:
    for position, seed in enumerate(PUBLIC_CREATORS):
        await subscriptions.configure_plan(
            db,
            profiles[seed.slug].id,
            "EUR",
            True,
            [
                {
                    "duration": "month_1",
                    "amount_minor": 999 + position * 50,
                    "enabled": True,
                },
                {
                    "duration": "month_3",
                    "amount_minor": 2_699 + position * 100,
                    "enabled": True,
                },
                {
                    "duration": "month_6",
                    "amount_minor": 4_999 + position * 150,
                    "enabled": True,
                },
                {
                    "duration": "month_12",
                    "amount_minor": 8_999 + position * 250,
                    "enabled": True,
                },
            ],
        )


async def _ensure_listings(
    db: AsyncSession,
    admin: User,
    users: dict[str, User],
    profiles: dict[str, CreatorProfile],
    provider,
    asset_root: Path,
) -> list[MarketplaceListing]:
    rows: list[MarketplaceListing] = []
    for creator_position, seed in enumerate(PUBLIC_CREATORS):
        creator = profiles[seed.slug]
        creator_user = users[seed.email]
        for item_position in range(listing_count_for_creator(creator_position)):
            marketplace_asset = await ensure_image_asset(
                db,
                creator_user,
                creator,
                seed.slug,
                provider,
                asset_root,
                variant=f"marketplace-{item_position}",
                audience=MediaAudience.safe_public,
                classification_actor=admin,
            )
            title = listing_title(seed, item_position)
            listing = await db.scalar(
                select(MarketplaceListing).where(
                    MarketplaceListing.owner_creator_id == creator.id,
                    MarketplaceListing.title == title,
                )
            )
            if not listing:
                listing = await marketplace.create_listing(
                    db,
                    creator_user,
                    creator_id=creator.id,
                    title=title,
                    description="A harmless fictional physical item for local marketplace testing.",
                    category="collectibles",
                    condition="new",
                    quantity_available=10,
                    price_amount_minor=1_500 + creator_position * 100 + item_position * 250,
                    currency="EUR",
                    shipping_mode="worldwide",
                    origin_country_code="PT",
                    shipping_charged_minor=350,
                    media_asset_ids=[marketplace_asset.id],
                )
            else:
                links = (
                    await db.scalars(
                        select(MarketplaceListingMedia)
                        .where(MarketplaceListingMedia.listing_id == listing.id)
                        .order_by(MarketplaceListingMedia.position)
                    )
                ).all()
                if links:
                    links[0].media_asset_id = marketplace_asset.id
                    links[0].position = 0
                    for extra in links[1:]:
                        await db.delete(extra)
                else:
                    db.add(
                        MarketplaceListingMedia(
                            listing_id=listing.id,
                            media_asset_id=marketplace_asset.id,
                            position=0,
                        )
                    )
            if listing.status in {
                MarketplaceListingStatus.draft,
                MarketplaceListingStatus.paused,
            }:
                await marketplace.submit_listing_for_review(
                    db, creator_user, listing.id, creator.id
                )
            if listing.status is MarketplaceListingStatus.pending_review:
                await marketplace.decide_listing_moderation(db, admin, listing.id, True)
            rows.append(listing)
    return rows


async def _settle_attempt(db: AsyncSession, attempt: PaymentAttempt) -> None:
    if attempt.status is not PaymentStatus.pending:
        return
    payload, signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db, payload, signature)


async def _ensure_marketplace_configuration(db: AsyncSession, admin: User) -> None:
    hold = await db.scalar(
        select(MarketplaceEarningsHoldPolicy).where(
            MarketplaceEarningsHoldPolicy.seller_tier == MarketplaceSellerTier.new_seller
        )
    )
    if not hold or not hold.active or not hold.is_default or hold.hold_duration_seconds != 0:
        await marketplace.configure_hold_policy(
            db,
            admin,
            tier_value=MarketplaceSellerTier.new_seller.value,
            hold_duration_seconds=0,
            active=True,
            is_default=True,
        )
    allowance = await db.scalar(
        select(MarketplaceShippingAllowance).where(
            MarketplaceShippingAllowance.scope == ShippingAllowanceScope.global_,
            MarketplaceShippingAllowance.destination_code == "*",
            MarketplaceShippingAllowance.currency == "EUR",
        )
    )
    if not allowance or not allowance.active or allowance.allowed_shipping_minor != 500:
        await marketplace.configure_shipping_allowance(
            db,
            admin,
            country_code=None,
            region_code=None,
            currency="EUR",
            allowed_shipping_minor=500,
        )


async def _ensure_financial_examples(
    db: AsyncSession,
    admin: User,
    users: dict[str, User],
    profiles: dict[str, CreatorProfile],
    content: dict[str, CreatorContent],
    listings: list[MarketplaceListing],
) -> None:
    subscription_pairs = (
        ("subscriber", "ivy-ember"),
        ("fan01", "luna-sparks"),
        ("fan02", "mira-nova"),
        ("fan03", "aria-group"),
        ("fan04", "valentina-cruz"),
        ("marketing-in", "sera-kim"),
    )
    for fan_local, creator_slug in subscription_pairs:
        subscription = await subscriptions.create_subscription(
            db,
            users[_email(fan_local)],
            profiles[creator_slug].id,
            "month_1",
            f"demo-subscription-{fan_local}-{creator_slug}",
        )
        period = await db.scalar(
            select(SubscriptionPeriod).where(
                SubscriptionPeriod.subscription_id == subscription.id,
                SubscriptionPeriod.sequence == 1,
            )
        )
        assert period
        attempt = await db.get(PaymentAttempt, period.payment_attempt_id)
        assert attempt
        await _settle_attempt(db, attempt)

    ppv_creators = [
        seed.slug
        for position, seed in enumerate(PUBLIC_CREATORS)
        if _video_policy(position, seed) is AccessPolicy.ppv
    ]
    ppv_buyers = ("ppvbuyer", "fan05", "fan06", "fan07", "fan08", "fan09", "fan10")
    for buyer_local, creator_slug in zip(ppv_buyers, ppv_creators, strict=False):
        purchase = await finance.initiate_purchase(
            db,
            users[_email(buyer_local)],
            content[creator_slug].video.id,
            f"demo-ppv-{buyer_local}-{creator_slug}",
        )
        attempt = await db.get(PaymentAttempt, purchase.payment_attempt_id)
        assert attempt
        await _settle_attempt(db, attempt)

    await _ensure_marketplace_configuration(db, admin)
    listing_by_creator = {
        creator_slug: next(
            listing for listing in listings if listing.owner_creator_id == profiles[creator_slug].id
        )
        for creator_slug in ("nora-market", "aria-group", "valentina-cruz")
    }
    order_pairs = (
        ("marketbuyer", "nora-market"),
        ("fan11", "aria-group"),
        ("fan12", "valentina-cruz"),
    )
    for position, (buyer_local, creator_slug) in enumerate(order_pairs):
        buyer = users[_email(buyer_local)]
        order = await marketplace.initiate_order(
            db,
            buyer,
            listing_by_creator[creator_slug].id,
            1,
            "PT",
            f"demo-marketplace-{buyer_local}-{creator_slug}",
            shipping_address={
                "recipient_name": "Fictional Demo Buyer",
                "line1": "1 Demo Street",
                "line2": None,
                "city": "Lisbon",
                "region_code": None,
                "postal_code": "1000-001",
                "country_code": "PT",
            },
        )
        attempt = await db.get(PaymentAttempt, order.payment_attempt_id)
        assert attempt
        await _settle_attempt(db, attempt)
        seller = users[_email(creator_slug)]
        if position == 0 and order.status is MarketplaceOrderStatus.paid:
            await marketplace.mark_order_processing(db, order.id, seller, profiles[creator_slug].id)
        if position == 0 and order.status is MarketplaceOrderStatus.processing:
            await marketplace.mark_order_shipped(
                db,
                order.id,
                seller,
                profiles[creator_slug].id,
                "Demo Carrier",
                "DEMO-TRACKING-001",
            )
        if position == 0 and order.status is MarketplaceOrderStatus.shipped:
            await marketplace.confirm_order_delivery(db, order.id, buyer)
            await marketplace.release_order_earnings(db, order)
        if position == 1 and order.status is MarketplaceOrderStatus.paid:
            await marketplace.mark_order_processing(db, order.id, seller, profiles[creator_slug].id)


async def _ensure_featuring(
    db: AsyncSession,
    admin: User,
    users: dict[str, User],
    profiles: dict[str, CreatorProfile],
) -> None:
    surface = await featuring.create_surface(
        db, admin, FeatureSurfaceKind.discover_creators.value, 0
    )
    slot = await db.scalar(
        select(FeatureSlot).where(
            FeatureSlot.surface_id == surface.id,
            FeatureSlot.slot_key == "demo-creator-hero",
        )
    )
    if not slot:
        slot = await featuring.create_slot(
            db, admin, surface.id, "demo-creator-hero", 0, capacity=1
        )
    duration_seconds = 30 * 24 * 60 * 60
    price = await db.scalar(
        select(FeaturePrice)
        .where(
            FeaturePrice.slot_id == slot.id,
            FeaturePrice.target_type == FeatureTargetType.creator,
            FeaturePrice.duration_seconds == duration_seconds,
            FeaturePrice.active.is_(True),
        )
        .order_by(FeaturePrice.version.desc())
    )
    if not price:
        await featuring.create_price(
            db,
            admin,
            slot.id,
            FeatureTargetType.creator.value,
            duration_seconds,
            2_500,
            "EUR",
        )
    creator = profiles["luna-sparks"]
    creator_user = users[_email("luna-sparks")]
    booking = await db.scalar(
        select(FeatureBooking).where(
            FeatureBooking.purchaser_user_id == creator_user.id,
            FeatureBooking.idempotency_key == "demo-feature-luna-sparks",
        )
    )
    if not booking:
        booking = await featuring.create_booking(
            db,
            actor=creator_user,
            purchaser=creator_user,
            slot_id=slot.id,
            target_type=FeatureTargetType.creator.value,
            target_id=creator.id,
            starts_at=datetime.now(UTC) + timedelta(seconds=5),
            duration_seconds=duration_seconds,
            idempotency_key="demo-feature-luna-sparks",
        )
    if booking.status is FeatureBookingStatus.awaiting_payment:
        attempt = await featuring.initiate_payment(db, booking, creator_user)
        await _settle_attempt(db, attempt)
    if booking.status is FeatureBookingStatus.scheduled:
        await featuring.activate_due_bookings(db, booking.starts_at + timedelta(seconds=1))


async def _seed_stats(db: AsyncSession, profiles: dict[str, CreatorProfile]) -> SeedStats:
    creator_ids = [profile.id for profile in profiles.values()]
    public_ids = [profiles[seed.slug].id for seed in PUBLIC_CREATORS]
    user_count = int(
        await db.scalar(select(func.count(User.id)).where(User.email.in_([u.email for u in USERS])))
        or 0
    )
    creator_count = int(
        await db.scalar(
            select(func.count(CreatorProfile.id)).where(CreatorProfile.id.in_(creator_ids))
        )
        or 0
    )
    post_count = int(
        await db.scalar(
            select(func.count(FeedPost.id)).where(
                FeedPost.creator_id.in_(public_ids),
                FeedPost.status == FeedPostStatus.published,
            )
        )
        or 0
    )
    content_count = int(
        await db.scalar(
            select(func.count(ContentItem.id)).where(
                ContentItem.owner_creator_id.in_(public_ids),
                ContentItem.status == ContentStatus.published,
            )
        )
        or 0
    )
    listing_count = int(
        await db.scalar(
            select(func.count(MarketplaceListing.id)).where(
                MarketplaceListing.owner_creator_id.in_(public_ids),
                MarketplaceListing.status == MarketplaceListingStatus.published,
            )
        )
        or 0
    )
    active_story_count = int(
        await db.scalar(
            select(func.count(Story.id)).where(
                Story.creator_id.in_(public_ids),
                Story.status == StoryStatus.active,
                Story.expires_at > datetime.now(UTC),
            )
        )
        or 0
    )
    return SeedStats(
        user_count,
        creator_count,
        post_count,
        content_count,
        listing_count,
        active_story_count,
    )


async def seed_database(
    db: AsyncSession,
    provider,
    *,
    asset_root: Path = ASSET_ROOT,
) -> SeedStats:
    """Converge a supplied transaction onto the local demo manifest.

    ``seed()`` owns the development guard.  This injected form is intentionally
    available to PostgreSQL integration tests using an isolated test database and
    an in-memory storage provider.
    """

    users = {seed.email: await _ensure_user(db, seed.email, seed.roles) for seed in USERS}
    admin = users[_email("admin")]
    demo_policy = await _ensure_compliance_demo_policy(db, admin)
    await _ensure_demo_age_verifications(db, users, demo_policy)
    await _ensure_demo_legal_content(db, admin, users)
    await _ensure_marketing_preferences(db, users)
    profiles = {
        seed.slug: await _ensure_creator(db, admin, users[seed.email], seed) for seed in CREATORS
    }
    await _ensure_pending_creator_kyc(db, admin, profiles[RESTRICTED_CREATOR.slug])
    await _ensure_referral(db, users)
    await _ensure_groups(db, users, profiles)

    content: dict[str, CreatorContent] = {}
    posts: dict[str, list[FeedPost]] = {}
    for position, seed in enumerate(PUBLIC_CREATORS):
        content[seed.slug] = await _ensure_creator_content(
            db,
            admin,
            users[seed.email],
            profiles[seed.slug],
            seed,
            position,
            provider,
            asset_root,
        )
        posts[seed.slug] = await _ensure_posts(
            db,
            users[seed.email],
            profiles[seed.slug],
            seed,
            content[seed.slug],
        )
    await _ensure_showcase_galleries(db, admin, users, profiles, provider, asset_root)
    await _ensure_performer_consent_example(db, admin, users, profiles, content)
    await _ensure_stories(db, admin, users, profiles, provider, asset_root)
    await _ensure_social_graph(db, users, profiles, posts)
    await _ensure_conversations(db, users, profiles)
    await _ensure_live_history(db, users, profiles)
    await _ensure_subscription_plans(db, profiles)
    listings = await _ensure_listings(db, admin, users, profiles, provider, asset_root)
    await _ensure_financial_examples(db, admin, users, profiles, content, listings)
    await _ensure_featuring(db, admin, users, profiles)
    await db.flush()
    return await _seed_stats(db, profiles)
