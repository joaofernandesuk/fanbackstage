"""Create the local-only administrator used by browser E2E tests."""

import asyncio

from sqlalchemy import select

from app.accounts import service as accounts
from app.db.session import SessionLocal
from app.models.identity import User

EMAIL = "phase2-e2e-admin@example.com"
PASSWORD = "phase2-e2e-admin-password"


async def seed() -> None:
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == EMAIL))
        if not user:
            user, _ = await accounts.register(db, EMAIL, PASSWORD, None)
        if "admin" not in {role.name for role in user.roles}:
            await accounts.assign_role(db, user, "admin", user.id, None)
        await db.commit()


asyncio.run(seed())
