import re
import secrets
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.service import assign_role
from app.audit.service import record_event
from app.models.creator import (
    CreatorProfile,
    CreatorStatus,
    CreatorStatusHistory,
    CreatorUsernameHistory,
    CreatorVerification,
    VerificationStatus,
)
from app.models.identity import User

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


def normalize_username(value: str) -> str:
    normalized = value.strip().lower()
    if not USERNAME_RE.fullmatch(normalized) or normalized in RESERVED_USERNAMES:
        raise ValueError("Username is unavailable or invalid")
    return normalized


async def profile_for_user(db: AsyncSession, user_id: UUID) -> CreatorProfile | None:
    return await db.scalar(select(CreatorProfile).where(CreatorProfile.user_id == user_id))


async def get_or_create_profile(db: AsyncSession, user: User) -> CreatorProfile:
    profile = await profile_for_user(db, user.id)
    if profile:
        return profile
    profile = CreatorProfile(user_id=user.id)
    db.add(profile)
    await db.flush()
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
    if status not in TRANSITIONS.get(profile.status, set()):
        raise ValueError(f"Cannot transition from {profile.status.value} to {status.value}")
    previous = profile.status
    profile.status = status
    if status in {CreatorStatus.suspended, CreatorStatus.disabled, CreatorStatus.rejected}:
        profile.is_public = False
    if status == CreatorStatus.rejected:
        profile.rejection_reason = reason or "Application was not approved"
    if status == CreatorStatus.approved:
        user = await db.get(User, profile.user_id)
        assert user is not None
        await assign_role(db, user, "creator", actor_user_id, None)
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
        "bio",
        "country_code",
        "region",
        "city",
        "show_location",
        "timezone",
        "is_public",
    ):
        if key in values and values[key] is not None:
            setattr(profile, key, values[key])
    if profile.country_code:
        profile.country_code = profile.country_code.upper()
    if profile.is_public and profile.status != CreatorStatus.approved:
        raise ValueError("Only approved creators can make a profile public")


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
    verification = CreatorVerification(
        creator_profile_id=profile.id,
        provider="development",
        provider_reference=f"dev_{secrets.token_urlsafe(16)}",
        status=VerificationStatus.verified if adult else VerificationStatus.failed,
        adult_verified=adult,
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
