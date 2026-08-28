from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import Db, OptionalIdentity, RawCurrentIdentity
from app.compliance.age_verification import (
    AgeVerificationError,
    attach_anonymous_session,
    complete_browser_callback,
    latest_age_verification,
    start_age_verification,
)
from app.compliance.http import (
    request_country_from_trusted_proxy,
    resolve_request_compliance_decision,
    resolve_request_jurisdiction_with_evidence,
)
from app.compliance.policy import CompliancePolicyError, set_account_country
from app.compliance.types import JurisdictionSignals
from app.core.config import get_settings
from app.creators.service import latest_verification, profile_for_user
from app.models.compliance import (
    AgeVerificationStatus,
    ComplianceFeature,
    CountryRegistry,
)
from app.schemas.compliance import (
    AccountCountryTransitionInput,
    AgeVerificationStartInput,
    AgeVerificationStartResponse,
    AnonymousAttachResponse,
    ComplianceStatusResponse,
    CountryResponse,
    CreatorIdentitySummary,
    DecisionResponse,
    VerificationSummary,
)

router = APIRouter(prefix="/compliance", tags=["compliance"])


def _verification_summary(record) -> VerificationSummary:
    status = record.status
    if (
        status is AgeVerificationStatus.verified
        and record.expires_at is not None
        and record.expires_at <= datetime.now(UTC)
    ):
        status = AgeVerificationStatus.expired
    return VerificationSummary(
        verification_id=record.id,
        provider=record.provider,
        status=status,
        country_code=record.country_code,
        required_minimum_age=record.required_minimum_age,
        required_assurance_level=record.required_assurance_level,
        achieved_minimum_age=record.achieved_minimum_age,
        achieved_assurance_level=record.achieved_assurance_level,
        initiated_at=record.initiated_at,
        verified_at=record.verified_at,
        expires_at=record.expires_at,
        failure_reason_code=record.failure_reason_code,
        retryable=record.retryable,
    )


@router.get("/countries", response_model=list[CountryResponse])
async def enabled_countries(db: Db) -> list[CountryResponse]:
    rows = (
        await db.scalars(
            select(CountryRegistry)
            .where(CountryRegistry.enabled.is_(True))
            .order_by(CountryRegistry.name, CountryRegistry.code)
        )
    ).all()
    return [CountryResponse(code=row.code, name=row.name) for row in rows]


@router.put("/account-country")
async def change_own_account_country(
    payload: AccountCountryTransitionInput,
    request: Request,
    identity: RawCurrentIdentity,
    db: Db,
) -> dict[str, str]:
    """Recover from a genuine country move using current trusted GeoIP authority."""

    trusted_country = request_country_from_trusted_proxy(request)
    if trusted_country != payload.country_code:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "COUNTRY_CHANGE_AUTHORITY_REQUIRED",
                "message": "The selected country must match current trusted location evidence",
            },
        )
    try:
        user = await set_account_country(
            db,
            user_id=identity[0].id,
            country_code=payload.country_code,
            actor_user_id=identity[0].id,
            change_reason="Account holder confirmed current trusted country transition",
            source="trusted_request",
        )
        # A country transition changes the jurisdiction used for existing
        # protected-live authority. Persist its eviction intent in this same
        # transaction; the outbox invokes LiveKit only after the commit.
        from app.streaming.service import evict_user_from_active_live

        await evict_user_from_active_live(
            db,
            user.id,
            reason="account_country_changed",
            force=True,
        )
        await db.commit()
    except CompliancePolicyError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"country_code": user.country_code or payload.country_code}


@router.get("/decision", response_model=DecisionResponse)
async def compliance_decision(
    request: Request,
    db: Db,
    identity: OptionalIdentity,
    feature: ComplianceFeature = ComplianceFeature.platform_access,
    country_code: str | None = Query(default=None, min_length=2, max_length=2),
    adult_restricted: bool = False,
) -> DecisionResponse:
    decision = await resolve_request_compliance_decision(
        db,
        request,
        user=identity[0] if identity else None,
        feature=feature,
        adult_restricted=adult_restricted,
        signals=JurisdictionSignals(selected_country=country_code),
    )
    return DecisionResponse.model_validate(decision.public_dict())


@router.post("/age-verification/start", response_model=AgeVerificationStartResponse)
async def begin_age_verification(
    payload: AgeVerificationStartInput,
    request: Request,
    response: Response,
    identity: OptionalIdentity,
    db: Db,
) -> AgeVerificationStartResponse:
    user = identity[0] if identity else None
    country = await resolve_request_jurisdiction_with_evidence(
        db,
        request,
        user=user,
        signals=JurisdictionSignals(selected_country=payload.country_code),
    )
    if country is None:
        raise HTTPException(status_code=400, detail="Jurisdiction is unresolved or conflicting")
    settings = get_settings()
    try:
        started = await start_age_verification(
            db,
            user=user,
            country_code=country,
            safe_return_path=payload.return_path,
            anonymous_session_secret=request.cookies.get(settings.anonymous_compliance_cookie_name),
        )
        await db.commit()
    except AgeVerificationError as exc:
        await db.commit()
        raise HTTPException(
            status_code=503 if exc.retryable else 400,
            detail={"code": exc.code, "message": str(exc), "retryable": exc.retryable},
        ) from exc
    if started.anonymous_session_secret and started.anonymous_session_expires_at:
        response.set_cookie(
            settings.anonymous_compliance_cookie_name,
            started.anonymous_session_secret,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
            expires=started.anonymous_session_expires_at,
            max_age=settings.anonymous_compliance_session_ttl_hours * 3600,
            path="/",
        )
    record = started.record
    return AgeVerificationStartResponse(
        verification_id=record.id,
        provider=record.provider,
        status=record.status,
        authorization_url=started.authorization_url,
        country_code=record.country_code,
        required_minimum_age=record.required_minimum_age,
        required_assurance_level=record.required_assurance_level,
        anonymous_session_expires_at=started.anonymous_session_expires_at,
    )


@router.get("/age-verification/callback/{provider_name}")
async def age_verification_callback(
    provider_name: str,
    state: str,
    code: str,
    request: Request,
    db: Db,
) -> RedirectResponse:
    try:
        completed = await complete_browser_callback(
            db, provider_name=provider_name, state=state, code=code
        )
        await db.commit()
    except AgeVerificationError as exc:
        # Provider failures are normalized callback evidence. Commit any
        # replay/status record the lifecycle produced; invalid callbacks write nothing.
        await db.commit()
        raise HTTPException(
            status_code=503 if exc.retryable else 400,
            detail={"code": exc.code, "message": str(exc), "retryable": exc.retryable},
        ) from exc
    target = f"{get_settings().web_origin.rstrip('/')}{completed.safe_return_path}"
    response = RedirectResponse(target, status_code=303)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    response.headers["Referrer-Policy"] = "no-referrer"
    settings = get_settings()
    cookie_value = request.cookies.get(settings.anonymous_compliance_cookie_name)
    if cookie_value and completed.anonymous_session_expires_at:
        remaining = int(
            (completed.anonymous_session_expires_at - datetime.now(UTC)).total_seconds()
        )
        if remaining > 0:
            response.set_cookie(
                settings.anonymous_compliance_cookie_name,
                cookie_value,
                httponly=True,
                secure=settings.cookie_secure,
                samesite="lax",
                max_age=remaining,
                expires=completed.anonymous_session_expires_at,
                path="/",
            )
        else:
            response.delete_cookie(settings.anonymous_compliance_cookie_name, path="/")
    return response


@router.get("/age-verification/status", response_model=ComplianceStatusResponse)
async def age_verification_status(
    request: Request, identity: OptionalIdentity, db: Db
) -> ComplianceStatusResponse:
    settings = get_settings()
    user = identity[0] if identity else None
    record = await latest_age_verification(
        db,
        user=user,
        anonymous_session_secret=request.cookies.get(settings.anonymous_compliance_cookie_name),
    )
    creator_summary = None
    if user is not None:
        profile = await profile_for_user(db, user.id)
        creator_verification = (
            await latest_verification(db, profile.id) if profile is not None else None
        )
        if creator_verification is not None:
            creator_summary = CreatorIdentitySummary(
                status=creator_verification.status.value,
                provider=creator_verification.provider,
                identity_verified=creator_verification.identity_verified,
                adult_verified=creator_verification.adult_verified,
                country_code=creator_verification.country_code,
                verified_at=creator_verification.verified_at,
                expires_at=creator_verification.expires_at,
            )
    adult_media_decision = await resolve_request_compliance_decision(
        db,
        request,
        user=user,
        feature=ComplianceFeature.adult_media,
        adult_restricted=True,
    )
    return ComplianceStatusResponse(
        fan_age_verification=_verification_summary(record) if record else None,
        adult_media_decision=DecisionResponse.model_validate(adult_media_decision.public_dict()),
        creator_identity_verification=creator_summary,
    )


@router.post("/anonymous-session/attach", response_model=AnonymousAttachResponse)
async def attach_anonymous_compliance_session(
    request: Request, response: Response, identity: RawCurrentIdentity, db: Db
) -> AnonymousAttachResponse:
    secret = request.cookies.get(get_settings().anonymous_compliance_cookie_name)
    if not secret:
        raise HTTPException(status_code=400, detail="Anonymous compliance session is missing")
    try:
        await attach_anonymous_session(db, anonymous_session_secret=secret, user=identity[0])
        await db.commit()
    except AgeVerificationError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400,
            detail={"code": exc.code, "message": str(exc), "retryable": exc.retryable},
        ) from exc
    response.delete_cookie(get_settings().anonymous_compliance_cookie_name, path="/")
    return AnonymousAttachResponse()
