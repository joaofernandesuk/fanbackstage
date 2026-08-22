import secrets

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.api.deps import CurrentIdentity, Db
from app.core.config import get_settings
from app.referrals import service

router = APIRouter(prefix="/r", tags=["referrals"])


@router.get("/me/dashboard")
async def referral_dashboard(identity: CurrentIdentity, db: Db) -> dict:
    """Private, server-owned referral earnings and conversion dashboard."""
    return await service.dashboard(db, identity[0].id)


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
