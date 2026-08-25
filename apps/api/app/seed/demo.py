"""Create deterministic, harmless demo identities and creator discovery data.

This module deliberately uses domain services for identity, roles, creator state,
and follows.  It refuses every non-development environment before opening a DB
session, so the local-only credentials can never be created in production.
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from app.accounts import service as accounts
from app.core.config import get_settings
from app.creators import service as creators
from app.db.session import SessionLocal
from app.models.creator import CreatorProfile, CreatorStatus
from app.models.identity import User
from app.social import service as social

PASSWORD = "fanbackstage-demo-local-only"

USERS = (
    ("admin@demo.fanbackstage.local", ("admin", "super_admin")),
    ("moderator@demo.fanbackstage.local", ("moderator",)),
    # Sensitive evidence is intentionally restricted to the super-admin
    # permission already defined centrally; this is not a new broad role.
    ("evidence-moderator@demo.fanbackstage.local", ("moderator", "super_admin")),
    ("manager@demo.fanbackstage.local", ("manager",)),
    ("newfan@demo.fanbackstage.local", ()),
    ("subscriber@demo.fanbackstage.local", ()),
    ("ppvbuyer@demo.fanbackstage.local", ()),
    ("marketbuyer@demo.fanbackstage.local", ()),
    ("socialfan@demo.fanbackstage.local", ()),
    ("marketing-in@demo.fanbackstage.local", ()),
    ("marketing-out@demo.fanbackstage.local", ()),
)

CREATORS = (
    (
        "luna-sparks",
        "Luna Sparks",
        "Established fictional studio creator sharing colorful behind-the-scenes work.",
    ),
    ("mira-nova", "Mira Nova", "A fictional rising creator with new weekly posts."),
    ("ivy-ember", "Ivy Ember", "Subscription-focused fictional creator."),
    ("skye-live", "Skye Live", "Fictional creator ready for local LiveKit testing."),
    (
        "nora-market",
        "Nora Market",
        "Fictional marketplace seller with harmless studio merchandise.",
    ),
    ("aria-group", "Aria Group", "Fictional creator represented by a demo agency."),
    (
        "reya-restricted",
        "Reya Restricted",
        "Fictional restricted profile for Trust & Safety review.",
    ),
)


def _assert_development() -> None:
    settings = get_settings()
    if settings.environment != "development" or not settings.demo_seed_enabled:
        raise RuntimeError(
            "Demo seeding requires FANBACKSTAGE_ENVIRONMENT=development and "
            "FANBACKSTAGE_DEMO_SEED_ENABLED=true"
        )


async def _user(db, email: str, roles: tuple[str, ...]) -> User:
    user = await db.scalar(select(User).where(User.email == email))
    if not user:
        user, _ = await accounts.register(db, email, PASSWORD, None)
    user.email_verified_at = datetime.now(UTC)
    for role in roles:
        if role not in {item.name for item in user.roles}:
            await accounts.assign_role(db, user, role, user.id, None)
    return user


async def _creator(db, admin: User, username: str, display_name: str, bio: str) -> CreatorProfile:
    email = f"{username}@demo.fanbackstage.local"
    user = await _user(db, email, ())
    profile = await creators.get_or_create_profile(db, user)
    if not profile.username:
        await creators.update_profile(
            db,
            profile,
            {
                "username": username,
                "display_name": display_name,
                "bio": bio,
                "country_code": "PT",
                "timezone": "Europe/Lisbon",
            },
            user.id,
        )
    if profile.status is CreatorStatus.draft:
        await creators.submit(db, profile, user.id)
        await creators.development_verify(db, profile, True, admin.id)
        await creators.set_status(db, profile, CreatorStatus.approved, admin.id)
        await creators.update_profile(db, profile, {"is_public": True}, user.id)
    if username == "reya-restricted" and profile.status is CreatorStatus.approved:
        await creators.set_status(
            db, profile, CreatorStatus.suspended, admin.id, "Demo safety review"
        )
    return profile


async def seed() -> None:
    _assert_development()
    async with SessionLocal() as db:
        admin = await _user(db, USERS[0][0], USERS[0][1])
        people = {email: await _user(db, email, roles) for email, roles in USERS[1:]}
        profiles = [await _creator(db, admin, *creator) for creator in CREATORS]
        public_profiles = [p for p in profiles if p.status is CreatorStatus.approved]
        for index, fan in enumerate(people.values()):
            if public_profiles:
                await social.follow(db, fan, public_profiles[index % len(public_profiles)].id)
                if len(public_profiles) > 1 and index % 2 == 0:
                    await social.follow(
                        db, fan, public_profiles[(index + 1) % len(public_profiles)].id
                    )
        await db.commit()
    print(
        "Demo seed complete: local-only users, creator profiles, roles, and follow graph are ready."
    )


if __name__ == "__main__":
    asyncio.run(seed())
