"""Create the local-only administrator used by browser E2E tests."""

import asyncio

from sqlalchemy import select

from app.accounts import service as accounts
from app.accounts.adult_access import attest_account
from app.creators import service as creators
from app.db.session import SessionLocal
from app.models.creator import CreatorCategory, CreatorLanguage, CreatorStatus
from app.models.identity import User

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
            user, _ = await accounts.register(db, EMAIL, PASSWORD, None, adult_confirmed=True)
        user.email_verified_at = user.email_verified_at or accounts._now()
        attest_account(user)
        if "admin" not in {role.name for role in user.roles}:
            await accounts.assign_role(db, user, "admin", user.id, None)
        # Referral programmes and financial policy changes intentionally require
        # the restricted configure capability.  The isolated E2E operator must
        # therefore be a super-admin rather than weakening those API checks.
        if "super_admin" not in {role.name for role in user.roles}:
            await accounts.assign_role(db, user, "super_admin", user.id, None)
        creator_user = await db.scalar(select(User).where(User.email == CREATOR_EMAIL))
        if not creator_user:
            creator_user, _ = await accounts.register(
                db, CREATOR_EMAIL, CREATOR_PASSWORD, None, adult_confirmed=True
            )
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
                db, MANAGER_EMAIL, MANAGER_PASSWORD, None, adult_confirmed=True
            )
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
                    db, email, password, None, adult_confirmed=True
                )
            moderator.email_verified_at = moderator.email_verified_at or accounts._now()
            attest_account(moderator)
            if "moderator" not in {role.name for role in moderator.roles}:
                await accounts.assign_role(db, moderator, "moderator", user.id, None)
        await db.commit()


asyncio.run(seed())
