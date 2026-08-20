from uuid import UUID

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db, OptionalIdentity
from app.content.access import can_access_asset, can_access_preview
from app.core.config import get_settings
from app.media import service
from app.media.storage import storage_provider
from app.models.content import MediaAsset, MediaDerivative
from app.schemas.content import UploadIntent, UploadResponse
from app.worker.tasks import process_media_asset

router = APIRouter(prefix="/media", tags=["media"])


@router.post("/uploads", response_model=UploadResponse)
async def initiate_upload(
    payload: UploadIntent, identity: CurrentIdentity, db: Db
) -> UploadResponse:
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
async def finalize_upload(asset_id: UUID, identity: CurrentIdentity, db: Db) -> UploadResponse:
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
    derivative_id: UUID, identity: OptionalIdentity, db: Db
) -> RedirectResponse:
    derivative = await db.get(MediaDerivative, derivative_id)
    if (
        not derivative
        or derivative.status.value != "ready"
        or not await can_access_asset(
            db, derivative.media_asset_id, identity[0] if identity else None
        )
    ):
        raise HTTPException(status_code=404, detail="Media not found")
    return RedirectResponse(
        storage_provider().create_download_url(
            derivative.storage_key, get_settings().media_url_ttl_seconds
        )
    )


@router.get("/previews/{derivative_id}")
async def preview_delivery(derivative_id: UUID, db: Db) -> RedirectResponse:
    derivative = await db.get(MediaDerivative, derivative_id)
    if not derivative or not await can_access_preview(db, derivative):
        raise HTTPException(status_code=404, detail="Media not found")
    return RedirectResponse(
        storage_provider().create_download_url(
            derivative.storage_key, get_settings().media_url_ttl_seconds
        )
    )
