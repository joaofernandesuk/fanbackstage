from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.api.deps import CurrentIdentity, Db, OptionalIdentity
from app.compliance.http import resolve_request_compliance_decision
from app.compliance.types import ComplianceDecision
from app.core.config import get_settings
from app.core.rate_limit import (
    enforce_discovery_rate_limit,
    enforce_media_rate_limit,
    enforce_social_rate_limit,
)
from app.media.contexts import has_single_media_context
from app.media.projection import safe_public_profile_media_reference
from app.media.storage import storage_provider
from app.models.compliance import ComplianceFeature
from app.models.content import MediaAsset, MediaStatus
from app.models.creator import CreatorProfile, CreatorVerification, VerificationStatus
from app.models.social import ReactionType
from app.models.story import Story, StoryReaction, StoryStatus
from app.schemas.social import ReactionInput
from app.schemas.story import (
    StoryCreate,
    StoryCreatorResponse,
    StoryMediaResponse,
    StoryRailResponse,
    StoryResponse,
)
from app.stories import service

router = APIRouter(prefix="/stories", tags=["stories"])


async def request_adult_access(
    db: Db,
    request: Request,
    identity,
    *,
    adult_restricted: bool = True,
) -> ComplianceDecision:
    return await resolve_request_compliance_decision(
        db,
        request,
        user=identity[0] if identity else None,
        feature=(
            ComplianceFeature.adult_media if adult_restricted else ComplianceFeature.platform_access
        ),
        adult_restricted=adult_restricted,
    )


async def story_response(
    db: Db,
    story: Story,
    compliance_decision: ComplianceDecision | None = None,
    platform_decision: ComplianceDecision | None = None,
    user=None,
) -> StoryResponse:
    creator = await db.get(CreatorProfile, story.creator_id)
    asset = await db.get(MediaAsset, story.media_asset_id)
    if (
        not creator
        or not creator.username
        or not asset
        or asset.owner_creator_id != story.creator_id
        or not await has_single_media_context(db, story.media_asset_id)
    ):
        raise HTTPException(status_code=404, detail="Story not found")
    # Story captions/alt text are creator-authored and have no independently
    # reviewed safe-public classification. The whole consumer Story therefore
    # remains age-restricted even when the underlying asset is marked safe.
    requires_adult = True
    decision = compliance_decision if requires_adult else platform_decision
    compliance_allowed = decision is None or decision.allowed
    derivative = await service.delivery_derivative(db, asset) if compliance_allowed else None
    if compliance_allowed and not derivative:
        raise HTTPException(status_code=404, detail="Story media not found")
    verification = await db.scalar(
        select(CreatorVerification)
        .where(CreatorVerification.creator_profile_id == creator.id)
        .order_by(CreatorVerification.created_at.desc())
        .limit(1)
    )
    reaction_rows = (
        await db.execute(
            select(StoryReaction.reaction_type, func.count())
            .where(StoryReaction.story_id == story.id)
            .group_by(StoryReaction.reaction_type)
        )
    ).all()
    reaction_counts = {reaction_type.value: int(count) for reaction_type, count in reaction_rows}
    viewer_reaction = None
    if user is not None:
        reaction = await db.scalar(
            select(StoryReaction).where(
                StoryReaction.story_id == story.id,
                StoryReaction.user_id == user.id,
            )
        )
        viewer_reaction = reaction.reaction_type.value if reaction else None
    return StoryResponse(
        id=story.id,
        status=story.status.value,
        creator=StoryCreatorResponse(
            id=creator.id,
            username=creator.username,
            display_name=creator.display_name or creator.username,
            avatar_reference=safe_public_profile_media_reference(creator.avatar_reference),
            verified=bool(
                verification
                and verification.status is VerificationStatus.verified
                and verification.adult_verified
            ),
        ),
        media_type=asset.media_type.value,
        caption=story.caption if compliance_allowed else None,
        alt_text=story.alt_text if compliance_allowed else None,
        access_policy=story.access_policy.value,
        created_at=story.created_at,
        published_at=story.published_at,
        expires_at=story.expires_at,
        media=(
            StoryMediaResponse(
                derivative_id=derivative.id,
                mime_type=derivative.mime_type,
                delivery_path=f"/stories/{story.id}/media",
            )
            if derivative
            else None
        ),
        reaction_count=sum(reaction_counts.values()),
        reaction_counts=reaction_counts,
        viewer_reaction=viewer_reaction,
        adult_access_required=requires_adult,
        adult_access_granted=not requires_adult
        or bool(decision is None or decision.age_access_allowed),
        compliance_allowed=compliance_allowed,
        compliance_code=decision.code if decision else "ALLOWED",
        compliance_action=(decision.action if decision and not compliance_allowed else None),
        compliance_reason=decision.reason if decision else None,
    )


@router.post("", response_model=StoryResponse, status_code=201)
async def create_story(
    payload: StoryCreate,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> StoryResponse:
    try:
        decision = await request_adult_access(db, request, identity)
        platform_decision = await request_adult_access(
            db, request, identity, adult_restricted=False
        )
        for access in (platform_decision, decision):
            if not access.allowed:
                raise HTTPException(
                    403,
                    {
                        "message": access.reason,
                        "code": access.code,
                        "action": access.action,
                        "reason": access.reason,
                    },
                )
        await enforce_social_rate_limit(request, str(identity[0].id), "story_create")
        story = await service.create_story(
            db,
            identity[0],
            payload.media_asset_id,
            payload.caption,
            payload.alt_text,
            payload.access_policy,
            idempotency_key or "",
        )
        await db.commit()
        return await story_response(db, story, decision, platform_decision, identity[0])
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400,
            detail=str(exc),
        ) from exc


@router.get("/mine", response_model=list[StoryResponse])
async def own_stories(
    request: Request,
    identity: CurrentIdentity,
    db: Db,
    status: StoryStatus | None = None,
    limit: int = Query(default=100, ge=1, le=100),
) -> list[StoryResponse]:
    try:
        await service.expire_due_stories(db)
        rows = await service.own_stories(db, identity[0], status, limit)
        await db.commit()
        decision = await request_adult_access(db, request, identity)
        platform_decision = await request_adult_access(
            db, request, identity, adult_restricted=False
        )
        return [
            await story_response(db, story, decision, platform_decision, identity[0])
            for story in rows
        ]
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/rail", response_model=StoryRailResponse)
async def public_rail(
    request: Request,
    identity: OptionalIdentity,
    db: Db,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=50),
    creator_username: str | None = Query(
        default=None, min_length=3, max_length=32, pattern=r"^[a-zA-Z][a-zA-Z0-9_-]+$"
    ),
) -> StoryRailResponse:
    try:
        await enforce_discovery_rate_limit(
            request, str(identity[0].id) if identity else "anonymous"
        )
        decision = await request_adult_access(db, request, identity)
        platform_decision = await request_adult_access(
            db, request, identity, adult_restricted=False
        )
        if platform_decision.allowed:
            rows, next_cursor = await service.public_rail(
                db,
                identity[0] if identity else None,
                cursor,
                limit,
                creator_username,
                access_decision=decision,
            )
        else:
            rows, next_cursor = [], None
        return StoryRailResponse(
            items=[
                await story_response(
                    db, story, decision, platform_decision, identity[0] if identity else None
                )
                for story in rows
            ],
            next_cursor=next_cursor,
            compliance_allowed=platform_decision.allowed,
            compliance_code=platform_decision.code,
            compliance_action=platform_decision.action,
            compliance_reason=platform_decision.reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{story_id}/media")
async def story_media(
    story_id: UUID,
    request: Request,
    identity: OptionalIdentity,
    db: Db,
) -> RedirectResponse:
    await enforce_media_rate_limit(request, str(identity[0].id) if identity else "anonymous")
    story = await db.get(Story, story_id)
    raw_asset = await db.get(MediaAsset, story.media_asset_id) if story else None
    if raw_asset and not await has_single_media_context(db, raw_asset.id):
        raise HTTPException(status_code=404, detail="Story not found")
    # A Story-level safe-public authority does not exist yet. Do not infer safe
    # caption/alt text from the media asset's audience label.
    restricted = bool(raw_asset)
    access_decision = await request_adult_access(db, request, identity, adult_restricted=restricted)
    owner_entitled = False
    if story and identity:
        creator = await db.get(CreatorProfile, story.creator_id)
        owner_entitled = bool(
            creator
            and creator.user_id == identity[0].id
            and story.status not in {StoryStatus.deleted, StoryStatus.removed}
        )
    if not owner_entitled:
        story = await service.public_story(
            db,
            story_id,
            identity[0] if identity else None,
            access_decision=access_decision,
        )
    if not story:
        raise HTTPException(status_code=404, detail="Story not found")
    asset = await db.get(MediaAsset, story.media_asset_id)
    if (
        not asset
        or asset.owner_creator_id != story.creator_id
        or asset.status is not MediaStatus.ready
        or asset.deleted_at is not None
        or asset.moderation_status in service.UNSAFE_MODERATION_STATUSES
    ):
        raise HTTPException(status_code=404, detail="Story media not found")
    # Ownership and subscription resolve entitlement only. The current
    # jurisdiction/age decision applies to every viewer, including the creator
    # and staff accounts, before a signed media location can be minted.
    if not access_decision.allowed:
        raise HTTPException(status_code=404, detail="Story not found")
    derivative = await service.delivery_derivative(db, asset)
    if not derivative:
        raise HTTPException(status_code=404, detail="Story media not found")
    configured_ttl = get_settings().media_url_ttl_seconds
    try:
        ttl = (
            configured_ttl if owner_entitled else service.public_delivery_ttl(story, configured_ttl)
        )
        if restricted and access_decision.verification_expires_at is not None:
            remaining = int(
                (access_decision.verification_expires_at - datetime.now(UTC)).total_seconds()
            )
            ttl = min(ttl, remaining)
            if ttl <= 0:
                raise ValueError("Adult Story access has expired")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Story not found") from exc
    return RedirectResponse(
        storage_provider().create_download_url(derivative.storage_key, ttl),
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/{story_id}", response_model=StoryResponse)
async def public_detail(
    story_id: UUID, request: Request, identity: OptionalIdentity, db: Db
) -> StoryResponse:
    decision = await request_adult_access(db, request, identity)
    platform_decision = await request_adult_access(db, request, identity, adult_restricted=False)
    story = await service.public_story(
        db,
        story_id,
        identity[0] if identity else None,
        access_decision=decision,
    )
    if not story:
        raise HTTPException(status_code=404, detail="Story not found")
    return await story_response(
        db, story, decision, platform_decision, identity[0] if identity else None
    )


@router.delete("/{story_id}", response_model=StoryResponse)
async def delete_story(
    story_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> StoryResponse:
    try:
        await enforce_social_rate_limit(request, str(identity[0].id), "story_delete")
        story = await service.delete_story(db, identity[0], story_id)
        await db.commit()
        decision = await request_adult_access(db, request, identity)
        platform_decision = await request_adult_access(
            db, request, identity, adult_restricted=False
        )
        return await story_response(db, story, decision, platform_decision, identity[0])
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc


async def _reactable_story(
    db: Db, request: Request, identity: CurrentIdentity, story_id: UUID
) -> Story:
    decision = await request_adult_access(db, request, identity)
    story = await service.public_story(
        db,
        story_id,
        identity[0],
        access_decision=decision,
    )
    if story is None:
        raise HTTPException(status_code=404, detail="Story not found")
    return story


@router.put("/{story_id}/reaction")
async def react_to_story(
    story_id: UUID,
    payload: ReactionInput,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
) -> dict:
    await enforce_social_rate_limit(request, str(identity[0].id), "story_reaction")
    await _reactable_story(db, request, identity, story_id)
    try:
        kind = ReactionType(payload.reaction_type)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid reaction") from exc
    reaction = await db.scalar(
        select(StoryReaction).where(
            StoryReaction.story_id == story_id,
            StoryReaction.user_id == identity[0].id,
        )
    )
    if reaction is None:
        db.add(StoryReaction(story_id=story_id, user_id=identity[0].id, reaction_type=kind))
    else:
        reaction.reaction_type = kind
    await db.commit()
    return {"reaction_type": kind.value}


@router.delete("/{story_id}/reaction")
async def unreact_to_story(
    story_id: UUID,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
) -> dict:
    await enforce_social_rate_limit(request, str(identity[0].id), "story_reaction")
    await _reactable_story(db, request, identity, story_id)
    reaction = await db.scalar(
        select(StoryReaction).where(
            StoryReaction.story_id == story_id,
            StoryReaction.user_id == identity[0].id,
        )
    )
    if reaction is not None:
        await db.delete(reaction)
    await db.commit()
    return {"removed": reaction is not None}
