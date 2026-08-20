from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentIdentity, Db
from app.media import service
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
        return UploadResponse(id=asset.id, status=asset.status.value, upload_url=upload_url)
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
        return UploadResponse(id=asset.id, status=asset.status.value)
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)
        ) from exc


@router.get("/mine", response_model=list[UploadResponse])
async def my_media(identity: CurrentIdentity, db: Db) -> list[UploadResponse]:
    from sqlalchemy import select

    from app.media.service import approved_creator
    from app.models.content import MediaAsset

    creator = await approved_creator(db, identity[0])
    rows = (
        await db.scalars(select(MediaAsset).where(MediaAsset.owner_creator_id == creator.id))
    ).all()
    return [UploadResponse(id=row.id, status=row.status.value) for row in rows]
