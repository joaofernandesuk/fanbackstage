import re
import secrets
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import exists, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.accounts.service import assign_role
from app.audit.service import record_event
from app.models.creator import (
    CreatorCategory,
    CreatorLanguage,
    CreatorProfile,
    CreatorSocialLink,
    CreatorStatus,
    CreatorStatusHistory,
    CreatorUsernameHistory,
    CreatorVerification,
    VerificationStatus,
)
from app.models.identity import User
from app.models.messaging import UserBlock

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


async def has_current_adult_verification(db: AsyncSession, creator_profile_id: UUID) -> bool:
    verification = await latest_verification(db, creator_profile_id)
    return bool(
        verification
        and verification.status is VerificationStatus.verified
        and verification.adult_verified
    )


async def require_current_adult_verification(
    db: AsyncSession, creator_profile_id: UUID
) -> CreatorVerification:
    verification = await latest_verification(db, creator_profile_id)
    if not (
        verification
        and verification.status is VerificationStatus.verified
        and verification.adult_verified
    ):
        raise ValueError("A current verified adult KYC outcome is required")
    return verification


async def require_public_creator_access(
    db: AsyncSession,
    creator_profile_id: UUID,
    viewer_user_id: UUID | None = None,
) -> CreatorProfile:
    """Require a currently public, approved, adult-verified creator relationship.

    This is a pre-charge/public-surface invariant. It deliberately includes
    two-way blocks when a viewer is known so payment cannot create access that
    the serving layer will immediately contain.
    """
    profile = await db.scalar(
        select(CreatorProfile).where(
            CreatorProfile.id == creator_profile_id,
            CreatorProfile.status == CreatorStatus.approved,
            CreatorProfile.is_public.is_(True),
            current_adult_verification_predicate(CreatorProfile.id),
        )
    )
    if not profile:
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
    if status not in TRANSITIONS.get(profile.status, set()):
        raise ValueError(f"Cannot transition from {profile.status.value} to {status.value}")
    if status is CreatorStatus.approved:
        await require_current_adult_verification(db, profile.id)
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
