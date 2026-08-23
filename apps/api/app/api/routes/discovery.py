from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db, OptionalIdentity
from app.core.rate_limit import enforce_discovery_rate_limit
from app.discovery import service
from app.models.discovery import DiscoveryHide
from app.permissions.policies import Permission, authorize
from app.schemas.discovery import DiscoveryConfigInput, DiscoveryHideInput, DiscoveryPage

router = APIRouter(prefix="/discovery", tags=["discovery"])


@router.get("/search", response_model=DiscoveryPage)
async def search(
    request: Request,
    db: Db,
    identity: OptionalIdentity,
    q: str | None = None,
    types: list[str] | None = None,
    cursor: str | None = None,
    limit: int = 20,
    category: str | None = None,
    live_now: bool = False,
    min_price: int | None = None,
    max_price: int | None = None,
    sort: str = "relevance",
) -> DiscoveryPage:
    user = identity[0] if identity else None
    await enforce_discovery_rate_limit(request, str(user.id) if user else "anonymous")
    try:
        items, next_cursor, version = await service.search(
            db,
            user,
            query=q,
            entity_types=set(types or []) or None,
            cursor=cursor,
            limit=min(max(limit, 1), 50),
            category=category,
            live_only=live_now,
            min_price=min_price,
            max_price=max_price,
            sort=sort,
        )
        await service.record_event(
            db,
            event_type="search",
            request_key=request.state.correlation_id,
            user=user,
            ranking_version=version,
        )
        await db.commit()
        return DiscoveryPage(items=items, next_cursor=next_cursor, ranking_version=version)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.get("/discover", response_model=DiscoveryPage)
async def discover(
    request: Request, db: Db, identity: OptionalIdentity, cursor: str | None = None, limit: int = 20
) -> DiscoveryPage:
    user = identity[0] if identity else None
    await enforce_discovery_rate_limit(request, str(user.id) if user else "anonymous")
    try:
        items, next_cursor, version = await service.search(
            db,
            user,
            query=None,
            cursor=cursor,
            limit=min(max(limit, 1), 50),
            sort="trending",
            feature_surface="discover_home_hero",
        )
        # Cold start intentionally uses deterministic globally eligible recent/live candidates.
        if user:
            for item in items:
                if item.reason == "RECENT":
                    item.reason = "RECENT"
        await service.record_event(
            db,
            event_type="recommendation_impression",
            request_key=request.state.correlation_id,
            user=user,
            ranking_version=version,
        )
        await db.commit()
        return DiscoveryPage(items=items, next_cursor=next_cursor, ranking_version=version)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.post("/events/click")
async def click(
    entity_type: str, entity_id: UUID, request: Request, identity: OptionalIdentity, db: Db
) -> dict:
    config = await service.current_config(db)
    try:
        await service.record_event(
            db,
            event_type="click",
            request_key=request.state.correlation_id,
            user=identity[0] if identity else None,
            ranking_version=config.version,
            entity_type=entity_type,
            entity_id=entity_id,
        )
        await db.commit()
        return {"recorded": True}
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.get("/admin/config")
async def get_config(identity: CurrentIdentity, db: Db) -> dict:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    row = await service.current_config(db)
    return {
        "version": row.version,
        "text_weight": row.text_weight,
        "live_boost": row.live_boost,
        "recency_weight": row.recency_weight,
        "engagement_weight": row.engagement_weight,
        "trending_window_hours": row.trending_window_hours,
        "default_result_limit": row.default_result_limit,
    }


@router.put("/admin/config")
async def put_config(payload: DiscoveryConfigInput, identity: CurrentIdentity, db: Db) -> dict:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    row = await service.update_config(db, identity[0], payload.model_dump())
    await db.commit()
    return {"version": row.version}


@router.get("/admin/hides")
async def list_hides(identity: CurrentIdentity, db: Db) -> list[dict]:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    rows = (await db.scalars(select(DiscoveryHide).order_by(DiscoveryHide.created_at.desc()))).all()
    return [
        {
            "id": str(row.id),
            "entity_type": row.entity_type.value,
            "entity_id": str(row.entity_id),
            "reason": row.reason,
        }
        for row in rows
    ]


@router.post("/admin/hides")
async def hide(payload: DiscoveryHideInput, identity: CurrentIdentity, db: Db) -> dict:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    try:
        row = await service.hide(
            db, identity[0], payload.entity_type, payload.entity_id, payload.reason
        )
        await db.commit()
        return {"id": str(row.id), "hidden": True}
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc
