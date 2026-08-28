from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentIdentity, Db
from app.models.compliance import (
    AgeVerificationStatus,
    PerformerIdentityStatus,
)
from app.performers import service
from app.permissions.policies import Permission, authorize
from app.schemas.performer import (
    ContentPerformerLinkInput,
    PerformerCreate,
    PerformerResponse,
    PerformerVerificationInput,
)

router = APIRouter(prefix="/performers", tags=["performers"])


def response(row) -> PerformerResponse:
    return PerformerResponse(
        id=row.id,
        safe_reference=row.safe_reference,
        platform_user_id=row.platform_user_id,
        country_code=row.country_code,
    )


@router.post("", response_model=PerformerResponse, status_code=201)
async def create(payload: PerformerCreate, identity: CurrentIdentity, db: Db) -> PerformerResponse:
    try:
        row = await service.create_identity(
            db,
            identity[0],
            payload.safe_reference,
            platform_user_id=payload.platform_user_id,
            country_code=payload.country_code,
        )
        await db.commit()
        return response(row)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.get("", response_model=list[PerformerResponse])
async def mine(identity: CurrentIdentity, db: Db) -> list[PerformerResponse]:
    try:
        return [response(row) for row in await service.owned_identities(db, identity[0])]
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.put("/content/{content_id}")
async def link_content(
    content_id: UUID,
    payload: ContentPerformerLinkInput,
    identity: CurrentIdentity,
    db: Db,
) -> dict:
    try:
        row = await service.link_content_performer(
            db,
            identity[0],
            content_id,
            payload.performer_id,
            payload.consent_release_id,
            identity_verification_required=payload.identity_verification_required,
            age_verification_required=payload.age_verification_required,
            release_required=payload.release_required,
        )
        await db.commit()
        return {"id": str(row.id), "content_id": str(row.content_id)}
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(403 if isinstance(exc, PermissionError) else 400, str(exc)) from exc


@router.post("/{performer_id}/identity-verifications")
async def identity_verification(
    performer_id: UUID,
    payload: PerformerVerificationInput,
    identity: CurrentIdentity,
    db: Db,
) -> dict:
    authorize(identity[0], Permission.COMPLIANCE_VERIFICATION_REVIEW)
    try:
        row = await service.record_identity_verification(
            db,
            identity[0],
            performer_id,
            provider=payload.provider,
            provider_reference=payload.provider_reference,
            status=PerformerIdentityStatus(payload.status),
            country_code=payload.country_code,
            expires_at=payload.expires_at,
            confirmed=payload.confirmed,
            reason=payload.reason,
        )
        await db.commit()
        return {"id": str(row.id), "status": row.status.value}
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.post("/{performer_id}/age-verifications")
async def age_verification(
    performer_id: UUID,
    payload: PerformerVerificationInput,
    identity: CurrentIdentity,
    db: Db,
) -> dict:
    authorize(identity[0], Permission.COMPLIANCE_VERIFICATION_REVIEW)
    if not payload.country_code:
        raise HTTPException(400, "Country is required for performer age verification")
    try:
        row = await service.record_age_verification(
            db,
            identity[0],
            performer_id,
            provider=payload.provider,
            provider_reference=payload.provider_reference,
            status=AgeVerificationStatus(payload.status),
            country_code=payload.country_code,
            required_minimum_age=payload.required_minimum_age,
            achieved_assurance_level=payload.achieved_assurance_level,
            expires_at=payload.expires_at,
            confirmed=payload.confirmed,
            reason=payload.reason,
        )
        await db.commit()
        return {"id": str(row.id), "status": row.status.value}
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc
