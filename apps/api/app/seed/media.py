"""Development-only media ingestion helpers for the deterministic demo.

Images exercise the normal upload/finalize/processing path.  The slim API image
does not contain ffmpeg, so the repository also contains tiny MP4 derivatives
rendered from the owned JPEG masters.  ``ensure_video_asset`` still exercises the
normal private upload/finalize boundary, then installs those already-rendered
derivatives as a narrowly scoped development seed adapter.  It never accepts
arbitrary paths or client-provided metadata.
"""

from __future__ import annotations

import io
from pathlib import Path
from uuid import UUID

from PIL import Image, ImageEnhance, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.media import service as media_service
from app.media.processing import process_media_asset
from app.media.storage import StorageProvider
from app.models.content import (
    DerivativeType,
    MediaAsset,
    MediaAudience,
    MediaDerivative,
    MediaStatus,
    ModerationStatus,
)
from app.models.creator import CreatorProfile
from app.models.identity import User

ASSET_ROOT = Path(__file__).with_name("assets")
VIDEO_WIDTH = 640
VIDEO_HEIGHT = 480
VIDEO_DURATION_SECONDS = 8
VIDEO_PREVIEW_DURATION_SECONDS = 2


def _variant_image_body(body: bytes, variant: str) -> bytes:
    """Create deterministic harmless gallery variety from a repository-owned master."""
    if not variant.startswith("gallery-showcase-"):
        return body
    position = int(variant.rsplit("-", 1)[-1])
    with Image.open(io.BytesIO(body)) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        if position % 2 == 0:
            image = ImageOps.mirror(image)
        if position == 2:
            image = ImageEnhance.Color(image).enhance(0.68)
        elif position == 3:
            image = ImageEnhance.Contrast(image).enhance(1.16)
        elif position == 4:
            image = ImageEnhance.Brightness(image).enhance(0.82)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=92, optimize=True)
        return output.getvalue()


async def _asset_by_filename(
    db: AsyncSession, creator: CreatorProfile, filename: str
) -> MediaAsset | None:
    return await db.scalar(
        select(MediaAsset)
        .where(
            MediaAsset.owner_creator_id == creator.id,
            MediaAsset.original_filename == filename,
            MediaAsset.deleted_at.is_(None),
        )
        .order_by(MediaAsset.created_at)
        .limit(1)
    )


async def ensure_image_asset(
    db: AsyncSession,
    user: User,
    creator: CreatorProfile,
    slug: str,
    provider: StorageProvider,
    asset_root: Path = ASSET_ROOT,
    *,
    variant: str = "studio",
    audience: MediaAudience = MediaAudience.safe_public,
    classification_actor: User,
) -> MediaAsset:
    filename = f"demo-{slug}-{variant}.jpg"
    asset = await _asset_by_filename(db, creator, filename)
    body = _variant_image_body((asset_root / f"{slug}.jpg").read_bytes(), variant)
    if not asset:
        asset, _ = await media_service.begin_upload(db, user, filename, "image/jpeg", provider)
    if asset.status is MediaStatus.pending_upload:
        provider.put(asset.storage_key, body, "image/jpeg")
        await media_service.finalize_upload(db, user, asset.id, provider)
    if asset.status is MediaStatus.failed:
        await media_service.requeue_failed_upload(db, user, asset.id)
    if asset.status in {MediaStatus.queued, MediaStatus.processing, MediaStatus.uploaded}:
        await process_media_asset(db, asset.id, provider)
    if asset.status is not MediaStatus.ready:
        raise RuntimeError(f"Demo image did not become ready: {slug}")
    asset.moderation_status = ModerationStatus.approved
    await media_service.classify_audience(db, classification_actor, asset.id, audience)
    return asset


async def _ready_derivative(
    db: AsyncSession,
    asset: MediaAsset,
    provider: StorageProvider,
    derivative_type: DerivativeType,
    body: bytes,
    mime_type: str,
    *,
    width: int,
    height: int,
    duration_seconds: int | None = None,
) -> MediaDerivative:
    row = await db.scalar(
        select(MediaDerivative).where(
            MediaDerivative.media_asset_id == asset.id,
            MediaDerivative.derivative_type == derivative_type,
        )
    )
    extension = "jpg" if mime_type == "image/jpeg" else "mp4"
    key = f"derivative/{asset.id}/{derivative_type.value}.{extension}"
    provider.put(key, body, mime_type)
    if not row:
        row = MediaDerivative(
            media_asset_id=asset.id,
            derivative_type=derivative_type,
            storage_key=key,
            mime_type=mime_type,
        )
        db.add(row)
    row.storage_key = key
    row.mime_type = mime_type
    row.status = MediaStatus.ready
    row.size_bytes = len(body)
    row.width = width
    row.height = height
    row.duration_seconds = duration_seconds
    return row


async def ensure_video_asset(
    db: AsyncSession,
    user: User,
    creator: CreatorProfile,
    slug: str,
    provider: StorageProvider,
    asset_root: Path = ASSET_ROOT,
    *,
    variant: str = "after-hours",
    audience: MediaAudience = MediaAudience.safe_public,
    classification_actor: User,
) -> MediaAsset:
    """Install an owned master and a genuinely shorter acquisition trailer."""

    filename = f"demo-{slug}-{variant}.mp4"
    asset = await _asset_by_filename(db, creator, filename)
    video_body = (asset_root / f"{slug}.mp4").read_bytes()
    preview_body = (asset_root / f"{slug}-preview.mp4").read_bytes()
    poster_body = (asset_root / f"{slug}.jpg").read_bytes()
    with Image.open(io.BytesIO(poster_body)) as image:
        poster_width, poster_height = image.size
    if not asset:
        asset, _ = await media_service.begin_upload(db, user, filename, "video/mp4", provider)
    if asset.status is MediaStatus.pending_upload:
        provider.put(asset.storage_key, video_body, "video/mp4")
        await media_service.finalize_upload(db, user, asset.id, provider)
    if asset.status not in {
        MediaStatus.queued,
        MediaStatus.processing,
        MediaStatus.uploaded,
        MediaStatus.ready,
    }:
        raise RuntimeError(f"Demo video cannot be installed from status {asset.status.value}")
    asset.status = MediaStatus.ready
    asset.moderation_status = ModerationStatus.approved
    asset.processing_error = None
    asset.size_bytes = len(video_body)
    asset.width = VIDEO_WIDTH
    asset.height = VIDEO_HEIGHT
    asset.duration_seconds = VIDEO_DURATION_SECONDS
    asset.checksum_sha256 = media_service.checksum(video_body)
    await media_service.classify_audience(db, classification_actor, asset.id, audience)
    await _ready_derivative(
        db,
        asset,
        provider,
        DerivativeType.poster,
        poster_body,
        "image/jpeg",
        width=poster_width,
        height=poster_height,
    )
    await _ready_derivative(
        db,
        asset,
        provider,
        DerivativeType.playback,
        video_body,
        "video/mp4",
        width=VIDEO_WIDTH,
        height=VIDEO_HEIGHT,
        duration_seconds=VIDEO_DURATION_SECONDS,
    )
    await _ready_derivative(
        db,
        asset,
        provider,
        DerivativeType.preview_clip,
        preview_body,
        "video/mp4",
        width=VIDEO_WIDTH,
        height=VIDEO_HEIGHT,
        duration_seconds=VIDEO_PREVIEW_DURATION_SECONDS,
    )
    await db.flush()
    return asset


async def restore_video_preview_ready(db: AsyncSession, asset_id: UUID) -> None:
    """Undo the normal preview queue marker for an already-rendered demo clip."""

    preview = await db.scalar(
        select(MediaDerivative).where(
            MediaDerivative.media_asset_id == asset_id,
            MediaDerivative.derivative_type == DerivativeType.preview_clip,
        )
    )
    if not preview:
        raise RuntimeError("Pre-rendered demo video preview is missing")
    preview.status = MediaStatus.ready
