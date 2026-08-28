from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.api.deps import CurrentIdentity, Db, OptionalIdentity
from app.compliance.http import resolve_request_compliance_decision
from app.compliance.types import ComplianceDecision
from app.content.access import can_access_content, content_requires_adult_access
from app.core.rate_limit import enforce_social_rate_limit
from app.models.compliance import ComplianceFeature
from app.models.content import ContentItem, DerivativeType, MediaAsset, MediaDerivative, MediaStatus
from app.models.creator import CreatorProfile
from app.models.social import (
    FeedPost,
    FeedPostMedia,
    FeedPostStatus,
    PostComment,
    PostReaction,
    ReactionType,
    SocialReport,
)
from app.schemas.social import (
    CommentInput,
    FeedPage,
    FeedPostInput,
    FeedPostResponse,
    FeedPostUpdate,
    FeedSettingsInput,
    ReactionInput,
    ReportInput,
)
from app.social import service
from app.stories import service as story_service

router = APIRouter(prefix="/feed", tags=["feed"])


async def post_response(
    db: Db,
    post: FeedPost,
    user,
    compliance_decision: ComplianceDecision | None = None,
    platform_decision: ComplianceDecision | None = None,
) -> FeedPostResponse:
    requires_adult = await service.post_requires_adult_access(db, post)
    adult_granted = not requires_adult or bool(
        compliance_decision and compliance_decision.age_access_allowed
    )
    decision = compliance_decision if requires_adult else platform_decision
    allowed = bool(
        decision
        and decision.allowed
        and await service.can_access_post(db, post, user, compliance_decision)
    )
    creator = await db.get(CreatorProfile, post.creator_id)
    reactions = (
        await db.scalar(
            select(func.count()).select_from(PostReaction).where(PostReaction.post_id == post.id)
        )
        or 0
    )
    comments = (
        await db.scalar(
            select(func.count())
            .select_from(PostComment)
            .where(
                PostComment.post_id == post.id,
                PostComment.deleted_at.is_(None),
                PostComment.hidden_at.is_(None),
            )
        )
        or 0
    )
    viewer_reaction = None
    if user:
        reaction = await db.scalar(
            select(PostReaction).where(
                PostReaction.post_id == post.id, PostReaction.user_id == user.id
            )
        )
        viewer_reaction = reaction.reaction_type.value if reaction else None
    media = []
    if allowed:
        entries = (
            await db.scalars(
                select(FeedPostMedia)
                .where(FeedPostMedia.post_id == post.id)
                .order_by(FeedPostMedia.position)
            )
        ).all()
        for entry in entries:
            asset = await db.get(MediaAsset, entry.media_asset_id)
            if not asset:
                continue
            derivative_type = (
                DerivativeType.playback
                if asset.media_type.value == "video"
                else DerivativeType.display
            )
            derivative = await db.scalar(
                select(MediaDerivative).where(
                    MediaDerivative.media_asset_id == asset.id,
                    MediaDerivative.derivative_type == derivative_type,
                    MediaDerivative.status == MediaStatus.ready,
                )
            )
            if derivative:
                media.append(
                    {
                        "derivative_id": str(derivative.id),
                        "delivery_path": f"/media/derivatives/{derivative.id}",
                        "media_type": asset.media_type.value,
                        "alt_text": entry.alt_text,
                    }
                )
    reference = None
    if post.source_content_id:
        content = await db.get(ContentItem, post.source_content_id)
        if content:
            content_requires_adult = await content_requires_adult_access(db, content)
            content_age_allowed = not content_requires_adult or bool(
                compliance_decision and compliance_decision.age_access_allowed
            )
            content_decision = compliance_decision if content_requires_adult else platform_decision
            content_compliance_allowed = bool(content_decision and content_decision.allowed)
            content_allowed = (
                await can_access_content(db, content, user)
                and content_age_allowed
                and content_compliance_allowed
            )
            reference = {
                "id": str(content.id),
                "title": (
                    content.title if content_compliance_allowed else "Age-restricted content"
                ),
                "content_type": content.content_type.value,
                "access_policy": content.access_policy.value,
                "locked": not content_allowed,
                "price_amount_minor": content.price_amount_minor
                if content.access_policy.value == "ppv"
                else None,
                "price_currency": content.price_currency
                if content.access_policy.value == "ppv"
                else None,
            }
    return FeedPostResponse(
        id=post.id,
        creator_id=post.creator_id,
        creator_username=creator.username or "",
        creator_name=creator.display_name or creator.username or "",
        post_type=post.post_type.value,
        body=post.body if allowed else None,
        status=post.status.value,
        access_policy=post.access_policy.value,
        locked=not allowed,
        published_at=post.published_at,
        pinned_at=post.pinned_at,
        comments_enabled=post.comments_enabled and allowed,
        reactions_enabled=post.reactions_enabled and allowed,
        reaction_count=reactions,
        comment_count=comments,
        viewer_reaction=viewer_reaction,
        media=media,
        content_reference=reference,
        adult_access_required=requires_adult,
        adult_access_granted=adult_granted,
        compliance_allowed=bool(decision and decision.allowed),
        compliance_code=decision.code if decision else "POLICY_UNAVAILABLE",
        compliance_action=(decision.action if decision and not decision.allowed else None),
        compliance_reason=decision.reason if decision else None,
    )


async def request_feed_decision(db: Db, request: Request, user) -> ComplianceDecision:
    return await resolve_request_compliance_decision(
        db,
        request,
        user=user,
        feature=ComplianceFeature.adult_media,
        adult_restricted=True,
    )


async def request_platform_decision(db: Db, request: Request, user) -> ComplianceDecision:
    return await resolve_request_compliance_decision(
        db,
        request,
        user=user,
        feature=ComplianceFeature.platform_access,
        adult_restricted=False,
    )


def require_feed_authoring(*decisions: ComplianceDecision) -> None:
    for decision in decisions:
        if not decision.allowed:
            raise HTTPException(
                403,
                {
                    "message": decision.reason,
                    "code": decision.code,
                    "action": decision.action,
                    "reason": decision.reason,
                },
            )


def feed_page_response(
    items: list[FeedPostResponse],
    next_cursor: str | None,
    platform_decision: ComplianceDecision,
) -> FeedPage:
    allowed = platform_decision.allowed
    return FeedPage(
        items=items if allowed else [],
        next_cursor=next_cursor if allowed else None,
        compliance_allowed=allowed,
        compliance_code=platform_decision.code,
        compliance_action=platform_decision.action if not allowed else None,
        compliance_reason=platform_decision.reason,
    )


@router.post("/creator/{creator_id}/follow")
async def follow(creator_id: UUID, request: Request, identity: CurrentIdentity, db: Db):
    try:
        require_feed_authoring(
            await request_platform_decision(db, request, identity[0]),
            await request_feed_decision(db, request, identity[0]),
        )
        await enforce_social_rate_limit(request, str(identity[0].id), "follow")
        created = await service.follow(db, identity[0], creator_id)
        await db.commit()
        return {"following": True, "created": created}
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(404 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.delete("/creator/{creator_id}/follow")
async def unfollow(creator_id: UUID, request: Request, identity: CurrentIdentity, db: Db):
    await enforce_social_rate_limit(request, str(identity[0].id), "follow")
    removed = await service.unfollow(db, identity[0], creator_id)
    await db.commit()
    return {"following": False, "removed": removed}


@router.get("/creator/{creator_id}/follow-state")
async def follow_state(creator_id: UUID, identity: OptionalIdentity, db: Db):
    if not identity:
        return {"following": False}
    return {
        "following": await db.scalar(
            select(service.Follow.id).where(
                service.Follow.user_id == identity[0].id, service.Follow.creator_id == creator_id
            )
        )
        is not None
    }


@router.post("/posts", response_model=FeedPostResponse)
async def create(payload: FeedPostInput, request: Request, identity: CurrentIdentity, db: Db):
    try:
        adult_decision = await request_feed_decision(db, request, identity[0])
        platform_decision = await request_platform_decision(db, request, identity[0])
        require_feed_authoring(platform_decision, adult_decision)
        await enforce_social_rate_limit(request, str(identity[0].id), "post")
        post = await service.create_post(db, identity[0], payload.model_dump())
        await db.commit()
        return await post_response(db, post, identity[0], adult_decision, platform_decision)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.patch("/posts/{post_id}", response_model=FeedPostResponse)
async def update(
    post_id: UUID,
    payload: FeedPostUpdate,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
):
    try:
        adult_decision = await request_feed_decision(db, request, identity[0])
        platform_decision = await request_platform_decision(db, request, identity[0])
        require_feed_authoring(platform_decision, adult_decision)
        post = await service.own_post(db, identity[0], post_id)
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(post, key, value)
        await db.commit()
        return await post_response(db, post, identity[0], adult_decision, platform_decision)
    except PermissionError as exc:
        await db.rollback()
        raise HTTPException(403, str(exc)) from exc


@router.post("/posts/{post_id}/publish", response_model=FeedPostResponse)
async def publish(post_id: UUID, request: Request, identity: CurrentIdentity, db: Db):
    try:
        adult_decision = await request_feed_decision(db, request, identity[0])
        platform_decision = await request_platform_decision(db, request, identity[0])
        require_feed_authoring(platform_decision, adult_decision)
        post = await service.publish(db, identity[0], post_id)
        await db.commit()
        return await post_response(db, post, identity[0], adult_decision, platform_decision)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/posts/{post_id}/archive")
async def archive(post_id: UUID, identity: CurrentIdentity, db: Db):
    post = await service.own_post(db, identity[0], post_id)
    post.status = FeedPostStatus.archived
    await db.commit()
    return {"id": str(post.id), "status": post.status.value}


@router.post("/posts/{post_id}/pin")
async def pin(post_id: UUID, identity: CurrentIdentity, db: Db):
    post = await service.own_post(db, identity[0], post_id)
    if post.status is not FeedPostStatus.published:
        raise HTTPException(400, "Only published posts can be pinned")
    old = (
        await db.scalars(
            select(FeedPost).where(
                FeedPost.creator_id == post.creator_id, FeedPost.pinned_at.is_not(None)
            )
        )
    ).all()
    for item in old:
        item.pinned_at = None
    post.pinned_at = datetime.now(UTC)
    await db.commit()
    return {"id": str(post.id), "pinned": True}


@router.delete("/posts/{post_id}/pin")
async def unpin(post_id: UUID, identity: CurrentIdentity, db: Db):
    post = await service.own_post(db, identity[0], post_id)
    post.pinned_at = None
    await db.commit()
    return {"id": str(post.id), "pinned": False}


@router.get("/following", response_model=FeedPage)
async def following(
    request: Request,
    identity: CurrentIdentity,
    db: Db,
    cursor: str | None = None,
    limit: int = 20,
):
    try:
        rows, next_cursor = await service.feed_posts(
            db, identity[0], "following", None, cursor, min(max(limit, 1), 50)
        )
        decision = await request_feed_decision(db, request, identity[0])
        platform_decision = await request_platform_decision(db, request, identity[0])
        return feed_page_response(
            [await post_response(db, x, identity[0], decision, platform_decision) for x in rows],
            next_cursor,
            platform_decision,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/mine", response_model=FeedPage)
async def mine(
    request: Request,
    identity: CurrentIdentity,
    db: Db,
    cursor: str | None = None,
    limit: int = 20,
):
    creator = await service.approved_creator(db, identity[0])
    rows = (
        await db.scalars(
            select(FeedPost)
            .where(FeedPost.creator_id == creator.id)
            .order_by(FeedPost.created_at.desc())
            .limit(min(max(limit, 1), 50))
        )
    ).all()
    decision = await request_feed_decision(db, request, identity[0])
    platform_decision = await request_platform_decision(db, request, identity[0])
    return feed_page_response(
        [await post_response(db, x, identity[0], decision, platform_decision) for x in rows],
        None,
        platform_decision,
    )


@router.get("/discover", response_model=FeedPage)
async def discover(
    request: Request,
    identity: OptionalIdentity,
    db: Db,
    cursor: str | None = None,
    limit: int = 20,
):
    try:
        rows, next_cursor = await service.feed_posts(
            db, identity[0] if identity else None, "discover", None, cursor, min(max(limit, 1), 50)
        )
        user = identity[0] if identity else None
        decision = await request_feed_decision(db, request, user)
        platform_decision = await request_platform_decision(db, request, user)
        return feed_page_response(
            [await post_response(db, x, user, decision, platform_decision) for x in rows],
            next_cursor,
            platform_decision,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/creator/{creator_id}", response_model=FeedPage)
async def profile_feed(
    creator_id: UUID,
    request: Request,
    identity: OptionalIdentity,
    db: Db,
    cursor: str | None = None,
    limit: int = 20,
):
    try:
        rows, next_cursor = await service.feed_posts(
            db,
            identity[0] if identity else None,
            "profile",
            creator_id,
            cursor,
            min(max(limit, 1), 50),
        )
        user = identity[0] if identity else None
        adult_decision = await request_feed_decision(db, request, user)
        platform_decision = await request_platform_decision(db, request, user)
        return feed_page_response(
            [await post_response(db, x, user, adult_decision, platform_decision) for x in rows],
            next_cursor,
            platform_decision,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/posts/{post_id}", response_model=FeedPostResponse)
async def detail(post_id: UUID, request: Request, identity: OptionalIdentity, db: Db):
    post = await db.get(FeedPost, post_id)
    if not post or post.status is not FeedPostStatus.published:
        raise HTTPException(404, "Post not found")
    try:
        await service.public_creator(db, post.creator_id)
    except PermissionError as exc:
        raise HTTPException(404, "Post not found") from exc
    user = identity[0] if identity else None
    platform_decision = await request_platform_decision(db, request, user)
    if not platform_decision.allowed:
        raise HTTPException(
            403,
            {
                "message": platform_decision.reason,
                "code": platform_decision.code,
                "action": platform_decision.action,
                "reason": platform_decision.reason,
            },
        )
    return await post_response(
        db,
        post,
        user,
        await request_feed_decision(db, request, user),
        platform_decision,
    )


@router.put("/posts/{post_id}/reaction")
async def react(
    post_id: UUID, payload: ReactionInput, request: Request, identity: CurrentIdentity, db: Db
):
    await enforce_social_rate_limit(request, str(identity[0].id), "reaction")
    post = await db.get(FeedPost, post_id)
    adult_decision = await request_feed_decision(db, request, identity[0])
    platform_decision = await request_platform_decision(db, request, identity[0])
    decision = (
        adult_decision
        if post and await service.post_requires_adult_access(db, post)
        else platform_decision
    )
    if (
        not post
        or not decision.allowed
        or not await service.can_access_post(db, post, identity[0], adult_decision)
        or not post.reactions_enabled
    ):
        raise HTTPException(403, "Reactions are unavailable")
    try:
        kind = ReactionType(payload.reaction_type)
    except ValueError as exc:
        raise HTTPException(400, "Invalid reaction") from exc
    reaction = await db.scalar(
        select(PostReaction).where(
            PostReaction.post_id == post_id, PostReaction.user_id == identity[0].id
        )
    )
    if reaction:
        reaction.reaction_type = kind
    else:
        db.add(PostReaction(post_id=post_id, user_id=identity[0].id, reaction_type=kind))
    await db.commit()
    return {"reaction_type": kind.value}


@router.delete("/posts/{post_id}/reaction")
async def unreact(post_id: UUID, request: Request, identity: CurrentIdentity, db: Db):
    await enforce_social_rate_limit(request, str(identity[0].id), "reaction")
    item = await db.scalar(
        select(PostReaction).where(
            PostReaction.post_id == post_id, PostReaction.user_id == identity[0].id
        )
    )
    if item:
        await db.delete(item)
    await db.commit()
    return {"removed": bool(item)}


@router.post("/posts/{post_id}/comments")
async def comment(
    post_id: UUID, payload: CommentInput, request: Request, identity: CurrentIdentity, db: Db
):
    await enforce_social_rate_limit(request, str(identity[0].id), "comment")
    post = await db.get(FeedPost, post_id)
    adult_decision = await request_feed_decision(db, request, identity[0])
    platform_decision = await request_platform_decision(db, request, identity[0])
    decision = (
        adult_decision
        if post and await service.post_requires_adult_access(db, post)
        else platform_decision
    )
    if (
        not post
        or not post.comments_enabled
        or not decision.allowed
        or not await service.can_access_post(db, post, identity[0], adult_decision)
    ):
        raise HTTPException(403, "Comments are unavailable")
    if payload.parent_id:
        parent = await db.get(PostComment, payload.parent_id)
        if not parent or parent.post_id != post_id or parent.parent_id is not None:
            raise HTTPException(400, "Invalid reply parent")
    value = PostComment(
        post_id=post_id, user_id=identity[0].id, parent_id=payload.parent_id, body=payload.body
    )
    db.add(value)
    await db.commit()
    return {
        "id": str(value.id),
        "parent_id": str(value.parent_id) if value.parent_id else None,
        "body": value.body,
    }


@router.get("/posts/{post_id}/comments")
async def comments(post_id: UUID, request: Request, identity: OptionalIdentity, db: Db):
    post = await db.get(FeedPost, post_id)
    user = identity[0] if identity else None
    adult_decision = await request_feed_decision(db, request, user)
    platform_decision = await request_platform_decision(db, request, user)
    decision = (
        adult_decision
        if post and await service.post_requires_adult_access(db, post)
        else platform_decision
    )
    if (
        not post
        or not decision.allowed
        or not await service.can_access_post(db, post, user, adult_decision)
    ):
        raise HTTPException(404, "Post not found")
    rows = (
        await db.scalars(
            select(PostComment)
            .where(
                PostComment.post_id == post_id,
                PostComment.deleted_at.is_(None),
                PostComment.hidden_at.is_(None),
            )
            .order_by(PostComment.created_at)
        )
    ).all()
    return [
        {
            "id": str(row.id),
            "user_id": str(row.user_id),
            "parent_id": str(row.parent_id) if row.parent_id else None,
            "body": row.body,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.delete("/comments/{comment_id}")
async def remove_comment(comment_id: UUID, identity: CurrentIdentity, db: Db):
    value = await db.get(PostComment, comment_id)
    if not value:
        raise HTTPException(404, "Comment not found")
    post = await db.get(FeedPost, value.post_id)
    owned_creator_id = await db.scalar(
        select(CreatorProfile.id).where(CreatorProfile.user_id == identity[0].id)
    )
    if value.user_id != identity[0].id and (not post or post.creator_id != owned_creator_id):
        raise HTTPException(403, "Comment not found")
    value.deleted_at = datetime.now(UTC)
    await db.commit()
    return {"deleted": True}


@router.post("/reports/{target_type}/{target_id}")
async def report(
    target_type: str,
    target_id: UUID,
    payload: ReportInput,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
):
    await enforce_social_rate_limit(request, str(identity[0].id), "report")
    if target_type not in {"post", "comment", "story"}:
        raise HTTPException(400, "Invalid report target")
    if target_type == "story":
        target = await story_service.public_story(db, target_id, identity[0])
    else:
        target = await db.get(FeedPost if target_type == "post" else PostComment, target_id)
    if not target:
        raise HTTPException(404, "Target not found")
    await db.execute(
        insert(SocialReport)
        .values(
            reporter_user_id=identity[0].id,
            target_type=target_type,
            target_id=target_id,
            reason=payload.reason,
            details=payload.details,
        )
        .on_conflict_do_nothing(
            index_elements=[
                SocialReport.reporter_user_id,
                SocialReport.target_type,
                SocialReport.target_id,
                SocialReport.reason,
            ]
        )
    )
    await db.commit()
    return {"reported": True}


@router.get("/settings", response_model=dict)
async def get_settings(identity: CurrentIdentity, db: Db):
    creator = await service.approved_creator(db, identity[0])
    value = await service.settings_for_creator(db, creator.id)
    await db.commit()
    return {
        "auto_post_galleries": value.auto_post_galleries,
        "auto_post_videos": value.auto_post_videos,
        "default_comments_enabled": value.default_comments_enabled,
    }


@router.patch("/settings", response_model=dict)
async def update_settings(payload: FeedSettingsInput, identity: CurrentIdentity, db: Db):
    creator = await service.approved_creator(db, identity[0])
    value = await service.settings_for_creator(db, creator.id)
    for key, item in payload.model_dump(exclude_unset=True).items():
        setattr(value, key, item)
    await db.commit()
    return {
        "auto_post_galleries": value.auto_post_galleries,
        "auto_post_videos": value.auto_post_videos,
        "default_comments_enabled": value.default_comments_enabled,
    }
