from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.accounts import adult_access
from app.api.deps import CurrentIdentity, Db, OptionalIdentity
from app.content.access import can_access_asset, can_access_preview
from app.core.config import get_settings
from app.core.rate_limit import enforce_media_rate_limit
from app.media import service
from app.media.storage import storage_provider
from app.models.content import MediaAsset, MediaAudience, MediaDerivative
from app.schemas.content import UploadIntent, UploadResponse
from app.worker.tasks import process_media_asset

router = APIRouter(prefix="/media", tags=["media"])


def _delivery_ttl(asset: MediaAsset, decision: adult_access.AdultAccessDecision) -> int:
    configured_ttl = get_settings().media_url_ttl_seconds
    if asset.audience is not MediaAudience.adult_restricted:
        return configured_ttl
    return adult_access.restricted_delivery_ttl(decision, configured_ttl)


@router.post("/uploads", response_model=UploadResponse)
async def initiate_upload(
    payload: UploadIntent, request: Request, identity: CurrentIdentity, db: Db
) -> UploadResponse:
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
        await db.scalars(select(MediaAsset).where(MediaAsset.owner_creator_id == creator.id))
    ).all()
    return [
        UploadResponse(id=row.id, status=row.status.value, media_type=row.media_type.value)
        for row in rows
    ]


@router.get("/derivatives/{derivative_id}")
async def derivative_delivery(
    derivative_id: UUID, request: Request, identity: OptionalIdentity, db: Db
) -> RedirectResponse:
    await enforce_media_rate_limit(request, str(identity[0].id) if identity else "anonymous")
    derivative = await db.get(MediaDerivative, derivative_id)
    decision = adult_access.resolve_adult_access(
        identity[0] if identity else None,
        request.cookies.get(get_settings().adult_access_cookie_name),
    )
    asset = await db.get(MediaAsset, derivative.media_asset_id) if derivative else None
    if (
        not derivative
        or not asset
        or derivative.status.value != "ready"
        or not await can_access_asset(
            db,
            derivative.media_asset_id,
            identity[0] if identity else None,
            decision,
        )
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
    decision = adult_access.resolve_adult_access(
        identity[0] if identity else None,
        request.cookies.get(get_settings().adult_access_cookie_name),
    )
    asset = await db.get(MediaAsset, derivative.media_asset_id) if derivative else None
    if (
        not derivative
        or not asset
        or not await can_access_preview(db, derivative, identity[0] if identity else None, decision)
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
