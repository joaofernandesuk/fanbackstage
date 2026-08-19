from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db
from app.core.config import get_settings
from app.creators import service
from app.models.creator import CreatorProfile, CreatorStatus, CreatorVerification
from app.schemas.creator import (
    CreatorProfileUpdate,
    CreatorSelfResponse,
    PublicCreatorResponse,
    SocialLinkInput,
    TaxonomyItem,
)

router = APIRouter(prefix="/creators", tags=["creators"])


async def self_response(db: Db, profile: CreatorProfile) -> CreatorSelfResponse:
    verification = await db.scalar(
        select(CreatorVerification)
        .where(CreatorVerification.creator_profile_id == profile.id)
        .order_by(CreatorVerification.created_at.desc())
    )
    return CreatorSelfResponse(
        id=profile.id,
        username=profile.username,
        display_name=profile.display_name,
        bio=profile.bio,
        country_code=profile.country_code,
        region=profile.region,
        city=profile.city,
        show_location=profile.show_location,
        timezone=profile.timezone,
        status=profile.status.value,
        is_public=profile.is_public,
        verification_status=verification.status.value if verification else "not_started",
        adult_verified=verification.adult_verified if verification else False,
        rejection_reason=profile.rejection_reason,
        languages=[
            TaxonomyItem(id=row.id, code=row.code, label=row.label) for row in profile.languages
        ],
        categories=[
            TaxonomyItem(id=row.id, code=row.slug, label=row.label) for row in profile.categories
        ],
        social_links=[SocialLinkInput(label=row.label, url=row.url) for row in profile.links],
    )


@router.post("/me/application", response_model=CreatorSelfResponse)
async def start_application(identity: CurrentIdentity, db: Db) -> CreatorSelfResponse:
    profile = await service.get_or_create_profile(db, identity[0])
    await db.commit()
    await db.refresh(profile, ["categories", "languages", "links"])
    return await self_response(db, profile)


@router.get("/me", response_model=CreatorSelfResponse)
async def own_profile(identity: CurrentIdentity, db: Db) -> CreatorSelfResponse:
    profile = await service.profile_for_user(db, identity[0].id)
    if not profile:
        raise HTTPException(status_code=404, detail="Creator application not found")
    return await self_response(db, profile)


@router.patch("/me", response_model=CreatorSelfResponse)
async def update_own_profile(
    payload: CreatorProfileUpdate, identity: CurrentIdentity, db: Db
) -> CreatorSelfResponse:
    profile = await service.profile_for_user(db, identity[0].id)
    if not profile:
        raise HTTPException(status_code=404, detail="Creator application not found")
    try:
        await service.update_profile(
            db, profile, payload.model_dump(exclude_unset=True), identity[0].id
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await db.refresh(profile, ["categories", "languages", "links"])
    return await self_response(db, profile)


@router.post("/me/submit", response_model=CreatorSelfResponse)
async def submit_application(identity: CurrentIdentity, db: Db) -> CreatorSelfResponse:
    profile = await service.profile_for_user(db, identity[0].id)
    if not profile:
        raise HTTPException(status_code=404, detail="Creator application not found")
    try:
        await service.submit(db, profile, identity[0].id)
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return await self_response(db, profile)


@router.post("/me/verification/development", response_model=CreatorSelfResponse)
async def development_verification(
    identity: CurrentIdentity, db: Db, adult: bool = True
) -> CreatorSelfResponse:
    if get_settings().environment == "production":
        raise HTTPException(status_code=404, detail="Not found")
    profile = await service.profile_for_user(db, identity[0].id)
    if not profile:
        raise HTTPException(status_code=404, detail="Creator application not found")
    await service.development_verify(db, profile, adult, identity[0].id)
    await db.commit()
    return await self_response(db, profile)


@router.get("/{username}", response_model=PublicCreatorResponse)
async def public_profile(username: str, db: Db) -> PublicCreatorResponse:
    profile = await db.scalar(
        select(CreatorProfile).where(
            CreatorProfile.username == username.lower(),
            CreatorProfile.status == CreatorStatus.approved,
            CreatorProfile.is_public.is_(True),
        )
    )
    if not profile:
        raise HTTPException(status_code=404, detail="Creator not found")
    location = None
    if profile.show_location:
        location = (
            ", ".join(item for item in (profile.city, profile.region, profile.country_code) if item)
            or None
        )
    return PublicCreatorResponse(
        id=profile.id,
        username=profile.username,
        display_name=profile.display_name or profile.username,
        bio=profile.bio,
        avatar_reference=profile.avatar_reference,
        cover_reference=profile.cover_reference,
        location=location,
        timezone=profile.timezone,
        verified=True,
        languages=[
            TaxonomyItem(id=row.id, code=row.code, label=row.label) for row in profile.languages
        ],
        categories=[
            TaxonomyItem(id=row.id, code=row.slug, label=row.label) for row in profile.categories
        ],
        social_links=[SocialLinkInput(label=row.label, url=row.url) for row in profile.links],
    )
