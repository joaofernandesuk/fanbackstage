from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db, OptionalIdentity
from app.compliance.http import resolve_request_compliance_decision
from app.compliance.types import ComplianceDecision
from app.content.access import asset_delivery_feature, can_access_asset, can_access_preview
from app.core.config import get_settings
from app.core.rate_limit import enforce_media_rate_limit
from app.media import service
from app.media.storage import storage_provider
from app.models.compliance import ComplianceFeature
from app.models.content import (
    DerivativeType,
    MediaAsset,
    MediaAudience,
    MediaDerivative,
    MediaStatus,
    MediaType,
    ModerationStatus,
)
from app.models.creator import CreatorProfile, CreatorProfileMedia, CreatorStatus
from app.schemas.content import UploadIntent, UploadResponse
from app.worker.tasks import process_media_asset

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/profile/{creator_id}/{kind}")
async def profile_media_delivery(
    creator_id: UUID, kind: str, request: Request, identity: OptionalIdentity, db: Db
) -> RedirectResponse:
    await enforce_media_rate_limit(
        request, str(identity[0].id) if identity else "anonymous"
    )
    if kind not in {"avatar", "cover"}:
        raise HTTPException(404, "Media not found")
    row = await db.scalar(
        select(CreatorProfileMedia)
        .join(CreatorProfile, CreatorProfile.id == CreatorProfileMedia.creator_profile_id)
        .where(
            CreatorProfileMedia.creator_profile_id == creator_id,
            CreatorProfileMedia.kind == kind,
            CreatorProfile.status == CreatorStatus.approved,
            CreatorProfile.is_public.is_(True),
        )
    )
    derivative = await db.scalar(
        select(MediaDerivative)
        .join(MediaAsset, MediaAsset.id == MediaDerivative.media_asset_id)
        .where(
            MediaDerivative.media_asset_id == row.media_asset_id if row else False,
            MediaDerivative.derivative_type == DerivativeType.display,
            MediaDerivative.status == MediaStatus.ready,
            MediaAsset.media_type == MediaType.image,
            MediaAsset.status == MediaStatus.ready,
            MediaAsset.moderation_status == ModerationStatus.approved,
            MediaAsset.audience == MediaAudience.safe_public,
            MediaAsset.deleted_at.is_(None),
        )
    )
    if row is None or derivative is None:
        raise HTTPException(404, "Media not found")
    return RedirectResponse(
        storage_provider().create_download_url(derivative.storage_key, get_settings().media_url_ttl_seconds),
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


async def require_media_authoring(db: Db, request: Request, user) -> None:
    for feature, restricted in (
        (ComplianceFeature.platform_access, False),
        (ComplianceFeature.adult_media, True),
    ):
        decision = await resolve_request_compliance_decision(
            db,
            request,
            user=user,
            feature=feature,
            adult_restricted=restricted,
        )
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


def _delivery_ttl(asset: MediaAsset, decision: ComplianceDecision) -> int:
    configured_ttl = get_settings().media_url_ttl_seconds
    if asset.audience is not MediaAudience.adult_restricted:
        return configured_ttl
    if not decision.allowed:
        raise ValueError("Adult media access is not allowed")
    verification_expires_at = getattr(decision, "verification_expires_at", None)
    if verification_expires_at is None:
        # Compatibility for already-issued signed self-attestation decisions.
        verification_expires_at = getattr(decision, "expires_at", None)
    if verification_expires_at is None:
        return configured_ttl
    remaining = int((verification_expires_at - datetime.now(UTC)).total_seconds())
    ttl = min(configured_ttl, remaining)
    if ttl <= 0:
        raise ValueError("Restricted media delivery is unavailable")
    return ttl


@router.post("/uploads", response_model=UploadResponse)
async def initiate_upload(
    payload: UploadIntent, request: Request, identity: CurrentIdentity, db: Db
) -> UploadResponse:
    await require_media_authoring(db, request, identity[0])
    await enforce_media_rate_limit(request, str(identity[0].id))
    try:
        asset, upload_url = await service.begin_upload(
            db, identity[0], payload.filename, payload.mime_type
        )
        await db.commit()
        return UploadResponse(
            id=asset.id,
            status=asset.status.value,
            media_type=asset.media_type.value,
            upload_url=upload_url,
        )
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)
        ) from exc


@router.post("/{asset_id}/finalize", response_model=UploadResponse)
async def finalize_upload(
    asset_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> UploadResponse:
    await require_media_authoring(db, request, identity[0])
    await enforce_media_rate_limit(request, str(identity[0].id))
    try:
        asset = await service.finalize_upload(db, identity[0], asset_id)
        await db.commit()
        if asset.status.value == "queued":
            process_media_asset.delay(str(asset.id))
        return UploadResponse(
            id=asset.id, status=asset.status.value, media_type=asset.media_type.value
        )
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)
        ) from exc


@router.post("/{asset_id}/requeue", response_model=UploadResponse)
async def requeue_upload(
    asset_id: UUID, request: Request, identity: CurrentIdentity, db: Db
) -> UploadResponse:
    await require_media_authoring(db, request, identity[0])
    await enforce_media_rate_limit(request, str(identity[0].id))
    try:
        asset = await service.requeue_failed_upload(db, identity[0], asset_id)
        await db.commit()
        process_media_asset.delay(str(asset.id))
        return UploadResponse(
            id=asset.id, status=asset.status.value, media_type=asset.media_type.value
        )
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)
        ) from exc


@router.get("/mine", response_model=list[UploadResponse])
async def my_media(identity: CurrentIdentity, db: Db) -> list[UploadResponse]:
    from app.media.service import approved_creator

    creator = await approved_creator(db, identity[0])
    rows = (
        await db.scalars(
            select(MediaAsset)
            .where(MediaAsset.owner_creator_id == creator.id)
            .order_by(MediaAsset.created_at, MediaAsset.id)
        )
    ).all()
    responses = []
    for row in rows:
        derivative = next(
            (
                item
                for item in row.derivatives
                if item.derivative_type is DerivativeType.display
                and item.status is MediaStatus.ready
            ),
            None,
        )
        responses.append(
            UploadResponse(
                id=row.id,
                status=row.status.value,
                media_type=row.media_type.value,
                display_path=f"/media/derivatives/{derivative.id}" if derivative else None,
            )
        )
    return responses


@router.get("/derivatives/{derivative_id}")
async def derivative_delivery(
    derivative_id: UUID, request: Request, identity: OptionalIdentity, db: Db
) -> RedirectResponse:
    await enforce_media_rate_limit(request, str(identity[0].id) if identity else "anonymous")
    derivative = await db.get(MediaDerivative, derivative_id)
    asset = await db.get(MediaAsset, derivative.media_asset_id) if derivative else None
    if not derivative or not asset or derivative.status.value != "ready":
        raise HTTPException(status_code=404, detail="Media not found")
    decision = await resolve_request_compliance_decision(
        db,
        request,
        user=identity[0] if identity else None,
        feature=await asset_delivery_feature(db, asset.id),
        adult_restricted=bool(asset and asset.audience is MediaAudience.adult_restricted),
    )
    if not await can_access_asset(
        db,
        derivative.media_asset_id,
        identity[0] if identity else None,
        decision,
    ):
        raise HTTPException(status_code=404, detail="Media not found")
    try:
        ttl = _delivery_ttl(asset, decision)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Media not found") from exc
    return RedirectResponse(
        storage_provider().create_download_url(derivative.storage_key, ttl),
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/previews/{derivative_id}")
async def preview_delivery(
    derivative_id: UUID, request: Request, identity: OptionalIdentity, db: Db
) -> RedirectResponse:
    await enforce_media_rate_limit(request)
    derivative = await db.get(MediaDerivative, derivative_id)
    asset = await db.get(MediaAsset, derivative.media_asset_id) if derivative else None
    if not derivative or not asset:
        raise HTTPException(status_code=404, detail="Media not found")
    decision = await resolve_request_compliance_decision(
        db,
        request,
        user=identity[0] if identity else None,
        feature=await asset_delivery_feature(db, asset.id),
        adult_restricted=bool(asset and asset.audience is MediaAudience.adult_restricted),
    )
    if not await can_access_preview(db, derivative, identity[0] if identity else None, decision):
        raise HTTPException(status_code=404, detail="Media not found")
    try:
        ttl = _delivery_ttl(asset, decision)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Media not found") from exc
    return RedirectResponse(
        storage_provider().create_download_url(derivative.storage_key, ttl),
        headers={"Cache-Control": "private, no-store", "Referrer-Policy": "no-referrer"},
    )
