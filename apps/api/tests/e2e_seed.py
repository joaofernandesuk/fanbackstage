"""Create the local-only administrator used by browser E2E tests."""

import asyncio

from sqlalchemy import select

from app.accounts import service as accounts
from app.db.session import SessionLocal
from app.models.identity import User

EMAIL = "phase2-e2e-admin@example.com"
PASSWORD = "phase2-e2e-admin-password"
MANAGER_EMAIL = "phase8-e2e-manager@example.com"
MANAGER_PASSWORD = "phase8-e2e-manager-password"
MODERATOR_EMAIL = "phase13-e2e-moderator@example.com"
MODERATOR_PASSWORD = "phase13-e2e-moderator-password"
REVIEWER_EMAIL = "phase13-e2e-reviewer@example.com"
REVIEWER_PASSWORD = "phase13-e2e-reviewer-password"


async def seed() -> None:
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == EMAIL))
        if not user:
            user, _ = await accounts.register(db, EMAIL, PASSWORD, None)
        if "admin" not in {role.name for role in user.roles}:
            await accounts.assign_role(db, user, "admin", user.id, None)
        # Referral programmes and financial policy changes intentionally require
        # the restricted configure capability.  The isolated E2E operator must
        # therefore be a super-admin rather than weakening those API checks.
        if "super_admin" not in {role.name for role in user.roles}:
            await accounts.assign_role(db, user, "super_admin", user.id, None)
        manager = await db.scalar(select(User).where(User.email == MANAGER_EMAIL))
        if not manager:
            manager, _ = await accounts.register(db, MANAGER_EMAIL, MANAGER_PASSWORD, None)
        if "manager" not in {role.name for role in manager.roles}:
            await accounts.assign_role(db, manager, "manager", user.id, None)
        for email, password in (
            (MODERATOR_EMAIL, MODERATOR_PASSWORD),
            (REVIEWER_EMAIL, REVIEWER_PASSWORD),
        ):
            moderator = await db.scalar(select(User).where(User.email == email))
            if not moderator:
                moderator, _ = await accounts.register(db, email, password, None)
            if "moderator" not in {role.name for role in moderator.roles}:
                await accounts.assign_role(db, moderator, "moderator", user.id, None)
        await db.commit()


asyncio.run(seed())
