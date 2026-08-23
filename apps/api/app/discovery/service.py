"""Discovery is a read projection: it never grants access or owns source state."""

import base64
import hashlib
import hmac
import json
import unicodedata
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event as record_audit_event
from app.content.access import can_access_content, can_access_preview
from app.core.config import get_settings
from app.models.content import (
    ContentItem,
    ContentStatus,
    DerivativeType,
    MediaDerivative,
    ModerationStatus,
)
from app.models.creator import CreatorProfile, CreatorStatus
from app.models.discovery import DiscoveryConfig, DiscoveryEntityType, DiscoveryEvent, DiscoveryHide
from app.models.identity import User
from app.models.marketplace import MarketplaceListing, MarketplaceListingStatus
from app.models.messaging import UserBlock
from app.models.social import FeedPost, FeedPostStatus, PostReaction
from app.models.streaming import LiveRoom, LiveRoomStatus
from app.schemas.discovery import DiscoveryResult

HIDDEN_MODERATION = (ModerationStatus.flagged, ModerationStatus.rejected, ModerationStatus.removed)


def normalize_query(value: str | None) -> str:
    return " ".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def _cursor(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(get_settings().session_secret.encode(), raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + sig).decode().rstrip("=")


def _parse_cursor(value: str | None) -> dict | None:
    if not value:
        return None
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        body, sig = raw[:-32], raw[-32:]
        if not hmac.compare_digest(
            sig, hmac.new(get_settings().session_secret.encode(), body, hashlib.sha256).digest()
        ):
            raise ValueError
        return json.loads(body)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid discovery cursor") from exc


async def current_config(db: AsyncSession) -> DiscoveryConfig:
    config = await db.scalar(
        select(DiscoveryConfig)
        .where(DiscoveryConfig.is_current.is_(True))
        .order_by(DiscoveryConfig.version.desc())
    )
    if config:
        return config
    config = DiscoveryConfig(version=1)
    db.add(config)
    await db.flush()
    return config


async def _blocked_creator_ids(db: AsyncSession, user: User | None) -> set[UUID]:
    if not user:
        return set()
    rows = await db.scalars(
        select(CreatorProfile.id).join(
            UserBlock,
            or_(
                and_(
                    UserBlock.blocker_user_id == user.id,
                    UserBlock.blocked_user_id == CreatorProfile.user_id,
                ),
                and_(
                    UserBlock.blocked_user_id == user.id,
                    UserBlock.blocker_user_id == CreatorProfile.user_id,
                ),
            ),
        )
    )
    return set(rows)


async def _hidden(db: AsyncSession) -> set[tuple[str, UUID]]:
    rows = (await db.execute(select(DiscoveryHide.entity_type, DiscoveryHide.entity_id))).all()
    return {(kind.value, identifier) for kind, identifier in rows}


def _matches(query: str, *values: str | None) -> bool:
    return not query or any(query in normalize_query(value) for value in values)


def _score(
    query: str,
    title: str,
    created_at: datetime,
    config: DiscoveryConfig,
    *,
    live: bool = False,
    engagement: int = 0,
) -> int:
    score = config.text_weight if query and query in normalize_query(title) else 0
    score += config.live_boost if live else 0
    age_hours = max(0, int((datetime.now(UTC) - created_at).total_seconds() // 3600))
    score += max(0, config.recency_weight - min(config.recency_weight, age_hours))
    return score + min(config.engagement_weight, engagement)


async def _creator_rows(
    db: AsyncSession,
    query: str,
    blocked: set[UUID],
    hidden: set[tuple[str, UUID]],
    live_only: bool,
    category: str | None,
) -> list[tuple[DiscoveryResult, int]]:
    rows = (
        await db.scalars(
            select(CreatorProfile).where(
                CreatorProfile.status == CreatorStatus.approved, CreatorProfile.is_public.is_(True)
            )
        )
    ).all()
    config = await current_config(db)
    output = []
    for row in rows:
        if (
            row.id in blocked
            or ("creator", row.id) in hidden
            or not _matches(query, row.username, row.display_name, row.bio)
        ):
            continue
        if category and category not in {item.slug for item in row.categories}:
            continue
        live = (
            await db.scalar(
                select(LiveRoom.id).where(
                    LiveRoom.creator_id == row.id, LiveRoom.status == LiveRoomStatus.live
                )
            )
            is not None
        )
        if live_only and not live:
            continue
        result = DiscoveryResult(
            entity_type="creator",
            id=row.id,
            title=row.display_name or row.username or "Creator",
            subtitle=f"@{row.username}" if row.username else None,
            description=(row.bio or "")[:280] or None,
            creator_id=row.id,
            creator_username=row.username,
            live=live,
            created_at=row.created_at,
            reason="LIVE_NOW" if live else "RECENT",
        )
        output.append((result, _score(query, result.title, row.created_at, config, live=live)))
    return output


async def _content_rows(
    db: AsyncSession,
    user: User | None,
    query: str,
    blocked: set[UUID],
    hidden: set[tuple[str, UUID]],
) -> list[tuple[DiscoveryResult, int]]:
    config = await current_config(db)
    output = []
    contents = (
        await db.scalars(
            select(ContentItem)
            .join(CreatorProfile, CreatorProfile.id == ContentItem.owner_creator_id)
            .where(
                ContentItem.status == ContentStatus.published,
                ContentItem.moderation_status.notin_(HIDDEN_MODERATION),
                CreatorProfile.status == CreatorStatus.approved,
                CreatorProfile.is_public.is_(True),
            )
        )
    ).all()
    for row in contents:
        kind = row.content_type.value
        if (
            row.owner_creator_id in blocked
            or (kind, row.id) in hidden
            or not _matches(query, row.title, row.description)
        ):
            continue
        creator = await db.get(CreatorProfile, row.owner_creator_id)
        preview_id = None
        # A discovery card uses only an authorised derivative identifier, never a storage key/URL.
        if row.video:
            derivative = await db.scalar(
                select(MediaDerivative).where(
                    MediaDerivative.media_asset_id == row.video.source_media_asset_id,
                    MediaDerivative.derivative_type.in_(
                        [DerivativeType.poster, DerivativeType.preview_clip]
                    ),
                )
            )
            if derivative and await can_access_preview(db, derivative):
                preview_id = derivative.id
        elif row.gallery and row.gallery.cover_media_asset_id:
            derivative = await db.scalar(
                select(MediaDerivative).where(
                    MediaDerivative.media_asset_id == row.gallery.cover_media_asset_id,
                    MediaDerivative.derivative_type == DerivativeType.blurred_preview,
                )
            )
            if derivative and await can_access_preview(db, derivative):
                preview_id = derivative.id
        allowed = await can_access_content(db, row, user)
        result = DiscoveryResult(
            entity_type=kind,
            id=row.id,
            title=row.title,
            subtitle=creator.display_name or creator.username if creator else None,
            description=(row.description or "")[:280] or None,
            creator_id=row.owner_creator_id,
            creator_username=creator.username if creator else None,
            access_policy=row.access_policy.value,
            locked=not allowed,
            preview_asset_id=preview_id,
            price_amount_minor=row.price_amount_minor if row.access_policy.value == "ppv" else None,
            currency=row.price_currency if row.access_policy.value == "ppv" else None,
            created_at=row.published_at or row.created_at,
            reason="RECENT",
        )
        output.append((result, _score(query, result.title, result.created_at, config)))
    posts = (
        await db.scalars(
            select(FeedPost)
            .join(CreatorProfile, CreatorProfile.id == FeedPost.creator_id)
            .where(
                FeedPost.status == FeedPostStatus.published,
                FeedPost.moderation_status.notin_(HIDDEN_MODERATION),
                CreatorProfile.status == CreatorStatus.approved,
                CreatorProfile.is_public.is_(True),
            )
        )
    ).all()
    for row in posts:
        if row.creator_id in blocked or ("post", row.id) in hidden or not _matches(query, row.body):
            continue
        creator = await db.get(CreatorProfile, row.creator_id)
        reactions = (
            await db.scalar(
                select(func.count()).select_from(PostReaction).where(PostReaction.post_id == row.id)
            )
            or 0
        )
        result = DiscoveryResult(
            entity_type="post",
            id=row.id,
            title=(row.body or "Post")[:160],
            subtitle=creator.display_name or creator.username if creator else None,
            description=(row.body or "")[:280] or None,
            creator_id=row.creator_id,
            creator_username=creator.username if creator else None,
            access_policy=row.access_policy.value,
            locked=not await __import__(
                "app.social.service", fromlist=["can_access_post"]
            ).can_access_post(db, row, user),
            created_at=row.published_at or row.created_at,
            reason="TRENDING" if reactions else "RECENT",
        )
        output.append(
            (result, _score(query, result.title, result.created_at, config, engagement=reactions))
        )
    return output


async def _listing_rows(
    db: AsyncSession,
    query: str,
    blocked: set[UUID],
    hidden: set[tuple[str, UUID]],
    category: str | None,
    min_price: int | None,
    max_price: int | None,
) -> list[tuple[DiscoveryResult, int]]:
    config = await current_config(db)
    output = []
    rows = (
        await db.scalars(
            select(MarketplaceListing)
            .join(CreatorProfile, CreatorProfile.id == MarketplaceListing.owner_creator_id)
            .where(
                MarketplaceListing.status.in_(
                    [MarketplaceListingStatus.published, MarketplaceListingStatus.sold_out]
                ),
                MarketplaceListing.moderation_status.notin_(HIDDEN_MODERATION),
                CreatorProfile.status == CreatorStatus.approved,
                CreatorProfile.is_public.is_(True),
            )
        )
    ).all()
    for row in rows:
        if (
            row.owner_creator_id in blocked
            or ("marketplace_listing", row.id) in hidden
            or not _matches(query, row.title, row.description, row.category)
        ):
            continue
        if (
            category
            and row.category != category
            or min_price is not None
            and row.price_amount_minor < min_price
            or max_price is not None
            and row.price_amount_minor > max_price
        ):
            continue
        creator = await db.get(CreatorProfile, row.owner_creator_id)
        availability = (
            "sold_out"
            if row.status is MarketplaceListingStatus.sold_out or row.quantity_available == 0
            else "available"
        )
        result = DiscoveryResult(
            entity_type="marketplace_listing",
            id=row.id,
            title=row.title,
            subtitle=creator.display_name or creator.username if creator else None,
            description=(row.description or "")[:280] or None,
            creator_id=row.owner_creator_id,
            creator_username=creator.username if creator else None,
            price_amount_minor=row.price_amount_minor,
            currency=row.currency,
            availability=availability,
            created_at=row.published_at or row.created_at,
            reason="RECENT",
        )
        output.append((result, _score(query, result.title, result.created_at, config)))
    return output


async def _live_rows(
    db: AsyncSession, query: str, blocked: set[UUID], hidden: set[tuple[str, UUID]]
) -> list[tuple[DiscoveryResult, int]]:
    config = await current_config(db)
    output = []
    rows = (
        await db.scalars(
            select(LiveRoom)
            .join(CreatorProfile, CreatorProfile.id == LiveRoom.creator_id)
            .where(
                LiveRoom.status == LiveRoomStatus.live,
                CreatorProfile.status == CreatorStatus.approved,
                CreatorProfile.is_public.is_(True),
            )
        )
    ).all()
    for row in rows:
        if (
            row.creator_id in blocked
            or ("live_room", row.id) in hidden
            or not _matches(query, row.title, row.description)
        ):
            continue
        creator = await db.get(CreatorProfile, row.creator_id)
        result = DiscoveryResult(
            entity_type="live_room",
            id=row.id,
            title=row.title,
            subtitle=creator.display_name or creator.username if creator else None,
            description=(row.description or "")[:280] or None,
            creator_id=row.creator_id,
            creator_username=creator.username if creator else None,
            access_policy=row.access_mode.value,
            live=True,
            started_at=row.started_at,
            created_at=row.started_at or row.created_at,
            reason="LIVE_NOW",
        )
        output.append(
            (
                result,
                _score(
                    query,
                    result.title,
                    result.created_at,
                    config,
                    live=True,
                    engagement=row.viewer_count,
                ),
            )
        )
    return output


async def search(
    db: AsyncSession,
    user: User | None,
    *,
    query: str | None,
    entity_types: set[str] | None = None,
    cursor: str | None = None,
    limit: int = 20,
    category: str | None = None,
    live_only: bool = False,
    min_price: int | None = None,
    max_price: int | None = None,
    sort: str = "relevance",
) -> tuple[list[DiscoveryResult], str | None, int]:
    text = normalize_query(query)
    if text and len(text) < 2:
        raise ValueError("Search query must contain at least two characters")
    if min_price is not None and max_price is not None and min_price > max_price:
        raise ValueError("Invalid price range")
    parsed = _parse_cursor(cursor)
    config = await current_config(db)
    if parsed and parsed.get("v") != config.version:
        raise ValueError("Discovery cursor is stale")
    types = entity_types or {kind.value for kind in DiscoveryEntityType}
    unknown = types - {kind.value for kind in DiscoveryEntityType}
    if unknown:
        raise ValueError("Invalid discovery entity type")
    blocked, hidden = await _blocked_creator_ids(db, user), await _hidden(db)
    candidates: list[tuple[DiscoveryResult, int]] = []
    if "creator" in types:
        candidates += await _creator_rows(db, text, blocked, hidden, live_only, category)
    if types & {"post", "video", "gallery"}:
        candidates += [
            (r, s)
            for r, s in await _content_rows(db, user, text, blocked, hidden)
            if r.entity_type in types
        ]
    if "marketplace_listing" in types:
        candidates += await _listing_rows(db, text, blocked, hidden, category, min_price, max_price)
    if "live_room" in types:
        candidates += await _live_rows(db, text, blocked, hidden)
    if sort == "newest":
        candidates.sort(key=lambda item: (item[0].created_at, str(item[0].id)), reverse=True)
    elif sort == "price_asc":
        candidates.sort(
            key=lambda item: (
                item[0].price_amount_minor is None,
                item[0].price_amount_minor or 0,
                str(item[0].id),
            )
        )
    elif sort == "price_desc":
        candidates.sort(
            key=lambda item: (item[0].price_amount_minor or -1, str(item[0].id)), reverse=True
        )
    elif sort == "live":
        candidates.sort(
            key=lambda item: (item[0].live, item[1], item[0].created_at, str(item[0].id)),
            reverse=True,
        )
    elif sort not in {"relevance", "trending"}:
        raise ValueError("Invalid discovery sort")
    else:
        candidates.sort(
            key=lambda item: (item[1], item[0].created_at, str(item[0].id)), reverse=True
        )
    start = int(parsed.get("i", 0)) if parsed else 0
    page = candidates[start : start + limit]
    has_more = start + limit < len(candidates)
    return (
        [row for row, _ in page],
        _cursor({"v": config.version, "i": start + limit}) if has_more else None,
        config.version,
    )


async def record_event(
    db: AsyncSession,
    *,
    event_type: str,
    request_key: str,
    user: User | None,
    ranking_version: int,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
) -> None:
    kind = DiscoveryEntityType(entity_type) if entity_type else None
    existing = await db.scalar(
        select(DiscoveryEvent).where(
            DiscoveryEvent.event_type == event_type,
            DiscoveryEvent.request_key == request_key,
            DiscoveryEvent.entity_type == kind,
            DiscoveryEvent.entity_id == entity_id,
        )
    )
    if not existing:
        db.add(
            DiscoveryEvent(
                event_type=event_type,
                request_key=request_key,
                actor_user_id=user.id if user else None,
                entity_type=kind,
                entity_id=entity_id,
                ranking_version=ranking_version,
            )
        )


async def update_config(db: AsyncSession, actor: User, values: dict) -> DiscoveryConfig:
    previous = await current_config(db)
    previous.is_current = False
    config = DiscoveryConfig(version=previous.version + 1, **values)
    db.add(config)
    await db.flush()
    await record_audit_event(
        db,
        "discovery.config_changed",
        actor_user_id=actor.id,
        target_type="discovery_config",
        target_id=str(config.id),
        metadata={"version": config.version},
    )
    return config


async def hide(
    db: AsyncSession, actor: User, entity_type: str, entity_id: UUID, reason: str
) -> DiscoveryHide:
    kind = DiscoveryEntityType(entity_type)
    row = await db.scalar(
        select(DiscoveryHide).where(
            DiscoveryHide.entity_type == kind, DiscoveryHide.entity_id == entity_id
        )
    )
    if row:
        return row
    row = DiscoveryHide(
        entity_type=kind, entity_id=entity_id, reason=reason, actor_user_id=actor.id
    )
    db.add(row)
    await db.flush()
    await record_audit_event(
        db,
        "discovery.hidden",
        actor_user_id=actor.id,
        target_type=kind.value,
        target_id=str(entity_id),
        metadata={"reason": reason},
    )
    return row
