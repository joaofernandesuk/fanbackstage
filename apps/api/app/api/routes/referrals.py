import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import or_, select

from app.api.deps import CurrentIdentity, Db
from app.core.config import get_settings
from app.models.creator import CreatorProfile
from app.models.referral import AffiliatePartner, ReferralCommissionAllocation
from app.referrals import service

router = APIRouter(prefix="/r", tags=["referrals"])


@router.get("/me/dashboard")
async def referral_dashboard(identity: CurrentIdentity, db: Db) -> dict:
    """Private referral earnings view; the caller can inspect only their own beneficiary rows."""
    creator = await db.scalar(
        select(CreatorProfile).where(CreatorProfile.user_id == identity[0].id)
    )
    affiliate_ids = list(
        await db.scalars(
            select(AffiliatePartner.id).where(AffiliatePartner.owner_user_id == identity[0].id)
        )
    )
    conditions = [ReferralCommissionAllocation.beneficiary_user_id == identity[0].id]
    if creator:
        conditions.append(ReferralCommissionAllocation.beneficiary_creator_id == creator.id)
    if affiliate_ids:
        conditions.append(
            ReferralCommissionAllocation.beneficiary_affiliate_partner_id.in_(affiliate_ids)
        )
    rows = (
        await db.scalars(
            select(ReferralCommissionAllocation)
            .where(or_(*conditions))
            .order_by(ReferralCommissionAllocation.allocated_at.desc())
        )
    ).all()
    return {
        "allocations": [
            {
                "id": str(row.id),
                "revenue_type": row.revenue_type,
                "currency": row.currency,
                "amount_minor": row.amount_minor,
                "platform_fee_minor": row.platform_fee_minor,
                "allocated_at": row.allocated_at,
                "released_at": row.released_at,
                "reversed_at": row.reversed_at,
            }
            for row in rows
        ]
    }


@router.get("/{code}", include_in_schema=False)
async def follow_referral(code: str, request: Request, db: Db) -> RedirectResponse:
    """Record a privacy-minimised first-party referral touch and redirect internally."""
    try:
        session_secret = request.cookies.get(
            "fanbackstage_referral_session"
        ) or secrets.token_urlsafe(24)
        link, token = await service.resolve_click(
            db,
            code,
            session_secret,
            source=request.query_params.get("source"),
            utm={
                key.removeprefix("utm_"): value
                for key, value in request.query_params.items()
                if key.startswith("utm_")
            },
        )
        await db.commit()
    except service.ReferralError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail="Referral link is unavailable") from exc
    settings = get_settings()
    response = RedirectResponse(link.destination_path, status_code=307)
    response.set_cookie(
        "fanbackstage_referral_session",
        session_secret,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=30 * 24 * 3600,
        path="/",
    )
    response.set_cookie(
        service.ATTRIBUTION_COOKIE_NAME,
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=30 * 24 * 3600,
        path="/",
    )
    return response
