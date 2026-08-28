"""Create the local-only administrator used by browser E2E tests."""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.accounts import service as accounts
from app.accounts.adult_access import attest_account
from app.compliance import policy as compliance_policy
from app.compliance.types import PolicyOverrides, PolicyRules
from app.creators import service as creators
from app.db.session import SessionLocal
from app.legal import service as legal_service
from app.models.compliance import (
    AgeAssuranceLevel,
    CompliancePolicyStatus,
    CompliancePolicyTemplate,
    CompliancePolicyTemplateRevision,
    CountryRegistry,
    JurisdictionPolicyRevision,
)
from app.models.creator import CreatorCategory, CreatorLanguage, CreatorStatus
from app.models.identity import User
from app.models.legal import (
    LegalAudience,
    LegalDocument,
    LegalDocumentType,
)

EMAIL = "phase2-e2e-admin@example.com"
PASSWORD = "phase2-e2e-admin-password"
MANAGER_EMAIL = "phase8-e2e-manager@example.com"
MANAGER_PASSWORD = "phase8-e2e-manager-password"
MODERATOR_EMAIL = "phase13-e2e-moderator@example.com"
MODERATOR_PASSWORD = "phase13-e2e-moderator-password"
REVIEWER_EMAIL = "phase13-e2e-reviewer@example.com"
REVIEWER_PASSWORD = "phase13-e2e-reviewer-password"
CREATOR_EMAIL = "consumer-e2e-creator@example.com"
CREATOR_PASSWORD = "consumer-e2e-creator-password"
CREATOR_USERNAME = "e2e-backstage-host"


async def seed_test_compliance_policy(db, actor: User) -> None:
    """Create reviewed test-only policy state without making a legal claim."""

    now = datetime.now(UTC)
    template = await db.scalar(
        select(CompliancePolicyTemplate).where(CompliancePolicyTemplate.key == "e2e-test-baseline")
    )
    if template is None:
        template = await compliance_policy.create_policy_template(
            db,
            key="e2e-test-baseline",
            name="E2E test baseline",
            description="Automated test configuration only; not a statement of law.",
            actor_user_id=actor.id,
            change_reason="Create deterministic isolated-browser policy",
        )
    revision = await db.scalar(
        select(CompliancePolicyTemplateRevision)
        .where(CompliancePolicyTemplateRevision.template_id == template.id)
        .order_by(CompliancePolicyTemplateRevision.version.desc())
        .limit(1)
    )
    if revision is None:
        revision = await compliance_policy.create_template_revision(
            db,
            template_id=template.id,
            rules=PolicyRules(
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
                creator_identity_required=False,
                creator_age_verification_required=False,
                payout_kyc_required=False,
                co_performer_verification_required=False,
                release_required=False,
                explicit_public_preview_allowed=True,
                restricted_media_policy="e2e-adult-restricted",
                age_provider="test",
                provider_policy_key=None,
            ),
            status=CompliancePolicyStatus.active,
            effective_from=now - timedelta(days=1),
            effective_until=None,
            actor_user_id=actor.id,
            reviewed_at=now,
            reviewed_by_user_id=actor.id,
            change_reason="Reviewed deterministic E2E policy fixture",
            is_demo=True,
        )
    jurisdiction = await db.scalar(
        select(JurisdictionPolicyRevision)
        .where(JurisdictionPolicyRevision.country_code == "PT")
        .order_by(JurisdictionPolicyRevision.version.desc())
        .limit(1)
    )
    if jurisdiction is None:
        await compliance_policy.create_jurisdiction_revision(
            db,
            country_code="PT",
            template_revision_id=revision.id,
            overrides=PolicyOverrides(),
            status=CompliancePolicyStatus.active,
            effective_from=now - timedelta(days=1),
            effective_until=None,
            actor_user_id=actor.id,
            reviewed_at=now,
            reviewed_by_user_id=actor.id,
            change_reason="Reviewed PT E2E policy fixture; not legal guidance",
            is_demo=True,
        )
    registry = await db.get(CountryRegistry, "PT")
    if registry is not None and not registry.enabled:
        await compliance_policy.set_country_enabled(
            db,
            code="PT",
            enabled=True,
            actor_user_id=actor.id,
            change_reason="Enable deterministic PT E2E compliance fixture",
        )


async def seed_test_legal_documents(db, actor: User) -> list:
    """Publish placeholder demo versions that cannot be used in production."""

    fixtures = (
        (LegalDocumentType.terms, "terms", "E2E Test Terms"),
        (LegalDocumentType.privacy, "privacy", "E2E Test Privacy Notice"),
        (LegalDocumentType.age_policy, "age-policy", "E2E Test Age Policy"),
    )
    for document_type, slug, title in fixtures:
        document = await db.scalar(
            select(LegalDocument).where(
                LegalDocument.slug == slug,
                LegalDocument.jurisdiction_code.is_(None),
                LegalDocument.language == "en",
                LegalDocument.audience == LegalAudience.all_users,
            )
        )
        if document is not None:
            continue
        _, version = await legal_service.create_document(
            db,
            actor,
            {
                "document_type": document_type,
                "slug": slug,
                "jurisdiction_code": None,
                "language": "en",
                "audience": LegalAudience.all_users,
                "title": title,
                "body": [
                    {
                        "type": "callout",
                        "text": (
                            "Automated browser-test fixture only. This is not approved "
                            "production legal text or legal advice."
                        ),
                    }
                ],
                "requires_acceptance": True,
                "requires_legal_review": False,
                "approved_for_publication": True,
                "is_demo": True,
            },
        )
        await legal_service.publish_version(
            db,
            actor,
            version.id,
            reason="Publish isolated E2E placeholder",
        )
    requirements = await legal_service.prospective_registration_requirements(
        db, jurisdiction_code="PT"
    )
    if len(requirements) != len(fixtures):
        raise RuntimeError("E2E legal fixture did not resolve the exact required versions")
    return requirements


async def seed() -> None:
    async with SessionLocal() as db:
        category = await db.scalar(select(CreatorCategory).where(CreatorCategory.slug == "studio"))
        if not category:
            category = CreatorCategory(slug="studio", label="Studio", position=10)
            db.add(category)
        category.label = "Studio"
        category.enabled = True
        category.position = 10
        language = await db.scalar(select(CreatorLanguage).where(CreatorLanguage.code == "en"))
        if not language:
            language = CreatorLanguage(code="en", label="English")
            db.add(language)
        language.label = "English"
        language.enabled = True
        user = await db.scalar(select(User).where(User.email == EMAIL))
        if not user:
            user, _ = await accounts.register(
                db, EMAIL, PASSWORD, None, adult_confirmed=True, country_code="PT"
            )
        user.country_code = "PT"
        user.email_verified_at = user.email_verified_at or accounts._now()
        attest_account(user)
        if "admin" not in {role.name for role in user.roles}:
            await accounts.assign_role(db, user, "admin", user.id, None)
        # Referral programmes and financial policy changes intentionally require
        # the restricted configure capability.  The isolated E2E operator must
        # therefore be a super-admin rather than weakening those API checks.
        if "super_admin" not in {role.name for role in user.roles}:
            await accounts.assign_role(db, user, "super_admin", user.id, None)
        await seed_test_compliance_policy(db, user)
        legal_requirements = await seed_test_legal_documents(db, user)
        creator_user = await db.scalar(select(User).where(User.email == CREATOR_EMAIL))
        if not creator_user:
            creator_user, _ = await accounts.register(
                db,
                CREATOR_EMAIL,
                CREATOR_PASSWORD,
                None,
                adult_confirmed=True,
                country_code="PT",
            )
        creator_user.country_code = "PT"
        creator_user.email_verified_at = creator_user.email_verified_at or accounts._now()
        attest_account(creator_user)
        profile = await creators.get_or_create_profile(db, creator_user)
        await creators.update_profile(
            db,
            profile,
            {
                "username": CREATOR_USERNAME,
                "display_name": "Backstage E2E Host",
                "bio": "Public creator fixture for real-stack consumer journeys.",
                "category_slugs": ["studio"],
                "language_codes": ["en"],
            },
            creator_user.id,
        )
        if profile.status is CreatorStatus.draft:
            await creators.submit(db, profile, creator_user.id)
        if profile.status is CreatorStatus.pending_verification:
            await creators.development_verify(db, profile, True, creator_user.id)
        if profile.status is CreatorStatus.pending_review:
            await creators.set_status(db, profile, CreatorStatus.approved, user.id)
        if profile.status is not CreatorStatus.approved:
            raise RuntimeError(f"Unexpected E2E creator status: {profile.status.value}")
        await creators.update_profile(
            db,
            profile,
            {"is_public": True},
            creator_user.id,
        )
        manager = await db.scalar(select(User).where(User.email == MANAGER_EMAIL))
        if not manager:
            manager, _ = await accounts.register(
                db,
                MANAGER_EMAIL,
                MANAGER_PASSWORD,
                None,
                adult_confirmed=True,
                country_code="PT",
            )
        manager.country_code = "PT"
        manager.email_verified_at = manager.email_verified_at or accounts._now()
        attest_account(manager)
        if "manager" not in {role.name for role in manager.roles}:
            await accounts.assign_role(db, manager, "manager", user.id, None)
        for email, password in (
            (MODERATOR_EMAIL, MODERATOR_PASSWORD),
            (REVIEWER_EMAIL, REVIEWER_PASSWORD),
        ):
            moderator = await db.scalar(select(User).where(User.email == email))
            if not moderator:
                moderator, _ = await accounts.register(
                    db,
                    email,
                    password,
                    None,
                    adult_confirmed=True,
                    country_code="PT",
                )
            moderator.country_code = "PT"
            moderator.email_verified_at = moderator.email_verified_at or accounts._now()
            attest_account(moderator)
            if "moderator" not in {role.name for role in moderator.roles}:
                await accounts.assign_role(db, moderator, "moderator", user.id, None)
        required_version_ids = [document.version_id for document in legal_requirements]
        seeded_users = list(
            await db.scalars(
                select(User).where(
                    User.email.in_(
                        [
                            EMAIL,
                            CREATOR_EMAIL,
                            MANAGER_EMAIL,
                            MODERATOR_EMAIL,
                            REVIEWER_EMAIL,
                        ]
                    )
                )
            )
        )
        for seeded_user in seeded_users:
            await legal_service.record_acceptances(
                db,
                seeded_user,
                required_version_ids,
                source="account",
                jurisdiction_code="PT",
                correlation_id=None,
            )
        await db.commit()


asyncio.run(seed())
