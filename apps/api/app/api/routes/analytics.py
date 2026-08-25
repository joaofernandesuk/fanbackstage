import csv
import io
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import select

from app.analytics import service
from app.api.deps import CurrentIdentity, Db
from app.audit.service import record_event
from app.groups import service as groups_service
from app.models.creator import CreatorProfile
from app.models.groups import Group
from app.permissions.policies import Permission, authorize

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _range(starts_at: datetime | None, ends_at: datetime | None) -> tuple[datetime, datetime]:
    end = (ends_at or datetime.now(UTC)).astimezone(UTC)
    start = (starts_at or end - timedelta(days=30)).astimezone(UTC)
    if start >= end or end - start > timedelta(days=366):
        raise HTTPException(400, "Date range must be positive and at most 366 days")
    return start, end


@router.get("/creator/overview")
async def creator_analytics_overview(
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    currency: str | None = None,
) -> dict:
    creator = await db.scalar(
        select(CreatorProfile).where(CreatorProfile.user_id == identity[0].id)
    )
    if not creator:
        raise HTTPException(403, "Creator analytics are unavailable")
    if currency and len(currency) != 3:
        raise HTTPException(400, "Currency must be a three-letter code")
    start, end = _range(starts_at, ends_at)
    return {
        "creator_id": str(creator.id),
        **(await service.creator_overview(db, creator.id, start, end, currency)),
    }


@router.get("/creator/audience")
async def creator_analytics_audience(
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> dict:
    creator = await db.scalar(
        select(CreatorProfile).where(CreatorProfile.user_id == identity[0].id)
    )
    if not creator:
        raise HTTPException(403, "Creator analytics are unavailable")
    start, end = _range(starts_at, ends_at)
    return {
        "creator_id": str(creator.id),
        **(await service.creator_audience(db, creator.id, start, end)),
    }


@router.get("/creator/subscriptions")
async def creator_analytics_subscriptions(
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> dict:
    creator = await db.scalar(
        select(CreatorProfile).where(CreatorProfile.user_id == identity[0].id)
    )
    if not creator:
        raise HTTPException(403, "Creator analytics are unavailable")
    start, end = _range(starts_at, ends_at)
    return {
        "creator_id": str(creator.id),
        **(await service.creator_subscription_metrics(db, creator.id, start, end)),
    }


@router.get("/creator/ppv")
async def creator_analytics_ppv(
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> dict:
    creator = await db.scalar(
        select(CreatorProfile).where(CreatorProfile.user_id == identity[0].id)
    )
    if not creator:
        raise HTTPException(403, "Creator analytics are unavailable")
    start, end = _range(starts_at, ends_at)
    return {
        "creator_id": str(creator.id),
        **(await service.creator_ppv_metrics(db, creator.id, start, end)),
    }


@router.get("/creator/marketplace")
async def creator_analytics_marketplace(
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> dict:
    creator = await db.scalar(
        select(CreatorProfile).where(CreatorProfile.user_id == identity[0].id)
    )
    if not creator:
        raise HTTPException(403, "Creator analytics are unavailable")
    start, end = _range(starts_at, ends_at)
    return {
        "creator_id": str(creator.id),
        **(await service.creator_marketplace_metrics(db, creator.id, start, end)),
    }


@router.get("/creator/content-performance")
async def creator_analytics_content_performance(
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> dict:
    creator = await _creator_scope(identity, db)
    start, end = _range(starts_at, ends_at)
    return {
        "creator_id": str(creator.id),
        **(await service.creator_content_performance(db, creator.id, start, end)),
    }


async def _creator_scope(identity: CurrentIdentity, db: Db) -> CreatorProfile:
    creator = await db.scalar(
        select(CreatorProfile).where(CreatorProfile.user_id == identity[0].id)
    )
    if not creator:
        raise HTTPException(403, "Creator analytics are unavailable")
    return creator


@router.get("/creator/messaging")
async def creator_analytics_messaging(
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> dict:
    creator = await _creator_scope(identity, db)
    start, end = _range(starts_at, ends_at)
    return {
        "creator_id": str(creator.id),
        **(await service.creator_messaging_metrics(db, creator.id, start, end)),
    }


@router.get("/creator/private-live")
async def creator_analytics_private_live(
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> dict:
    creator = await _creator_scope(identity, db)
    start, end = _range(starts_at, ends_at)
    return {
        "creator_id": str(creator.id),
        **(await service.creator_private_live_metrics(db, creator.id, start, end)),
    }


@router.get("/creator/referrals")
async def creator_analytics_referrals(
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> dict:
    creator = await _creator_scope(identity, db)
    start, end = _range(starts_at, ends_at)
    return {
        "creator_id": str(creator.id),
        **(await service.creator_referral_metrics(db, creator.id, start, end)),
    }


@router.get("/creator/featuring")
async def creator_analytics_featuring(
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> dict:
    creator = await _creator_scope(identity, db)
    start, end = _range(starts_at, ends_at)
    return {
        "creator_id": str(creator.id),
        **(await service.creator_featuring_metrics(db, creator.id, start, end)),
    }


@router.get("/platform/overview")
async def platform_analytics_overview(
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> dict:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    start, end = _range(starts_at, ends_at)
    return await service.platform_overview(db, start, end)


@router.get("/platform/growth")
async def platform_growth_analytics(
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> dict:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    start, end = _range(starts_at, ends_at)
    return await service.platform_growth_and_attribution(db, start, end)


@router.get("/platform/cohorts")
async def platform_cohorts_analytics(
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> dict:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    start, end = _range(starts_at, ends_at)
    return await service.platform_cohorts_retention_and_churn(db, start, end)


def _safe_cell(value: object) -> object:
    text = str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else value


@router.get("/creator/revenue-export.csv")
async def creator_revenue_export(
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> Response:
    creator = await _creator_scope(identity, db)
    start, end = _range(starts_at, ends_at)
    report = await service.creator_overview(db, creator.id, start, end)
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "currency",
            "source",
            "gross_sales_minor",
            "creator_net_minor",
            "reversed_minor",
        ],
    )
    writer.writeheader()
    for row in report["revenue_sources"][:50_000]:
        writer.writerow({key: _safe_cell(value) for key, value in row.items()})
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=creator-revenue.csv"},
    )


@router.get("/platform/detail-export.csv")
async def platform_detail_export(
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> Response:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    start, end = _range(starts_at, ends_at)
    report = await service.platform_overview(db, start, end)
    output = io.StringIO()
    fields = [
        "currency",
        "gmv_minor",
        "platform_fee_minor",
        "platform_retained_net_minor",
        "creator_distributable_minor",
        "group_distributable_minor",
        "refunds_minor",
        "chargebacks_minor",
        "referral_affiliate_commission_minor",
        "featuring_revenue_minor",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in report["currencies"][:50_000]:
        writer.writerow({key: _safe_cell(row.get(key, "")) for key in fields})
    await record_event(
        db,
        "analytics.platform_exported",
        actor_user_id=identity[0].id,
        target_type="analytics",
        metadata={"rows": len(report["currencies"])},
    )
    await db.commit()
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=platform-bi.csv"},
    )


@router.get("/groups/{group_id}/creators")
async def group_creator_analytics(
    group_id: str,
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> dict:
    from uuid import UUID

    try:
        resolved = UUID(group_id)
    except ValueError as exc:
        raise HTTPException(404, "Group not found") from exc
    group = await db.get(Group, resolved)
    if not group or not await groups_service.manager_membership(db, group.id, identity[0].id):
        raise HTTPException(403, "Group analytics permission denied")
    start, end = _range(starts_at, ends_at)
    creator_ids = await service.current_managed_creators(db, group.id, identity[0].id)
    return {
        "group_id": str(group.id),
        "active_managed_creator_ids": [str(item) for item in creator_ids],
        "creator_comparison": await service.group_creator_comparison(
            db, group.id, identity[0].id, start, end
        ),
    }


@router.get("/groups/{group_id}/overview")
async def group_analytics_overview(
    group_id: str,
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    currency: str | None = None,
) -> dict:
    from uuid import UUID

    try:
        resolved = UUID(group_id)
    except ValueError as exc:
        raise HTTPException(404, "Group not found") from exc
    group = await db.get(Group, resolved)
    if not group or not await groups_service.manager_membership(db, group.id, identity[0].id):
        raise HTTPException(403, "Group analytics permission denied")
    if currency and len(currency) != 3:
        raise HTTPException(400, "Currency must be a three-letter code")
    start, end = _range(starts_at, ends_at)
    return {
        "group_id": str(group.id),
        **(await service.group_overview(db, group.id, start, end, currency)),
    }


@router.get("/groups/{group_id}/revenue-export.csv")
async def group_revenue_export(
    group_id: str,
    identity: CurrentIdentity,
    db: Db,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
) -> Response:
    from uuid import UUID

    try:
        resolved = UUID(group_id)
    except ValueError as exc:
        raise HTTPException(404, "Group not found") from exc
    group = await db.get(Group, resolved)
    if not group or not await groups_service.manager_membership(db, group.id, identity[0].id):
        raise HTTPException(403, "Group analytics permission denied")
    start, end = _range(starts_at, ends_at)
    report = await service.group_overview(db, group.id, start, end)
    fields = ["currency", "source", "group_net_minor", "reversed_minor"]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in report["revenue_sources"][:50_000]:
        writer.writerow({key: _safe_cell(row.get(key, "")) for key in fields})
    return Response(
        output.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=group-revenue.csv"},
    )
