import io
import json
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

from PIL import Image, ImageFilter, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.media.service import checksum
from app.media.storage import StorageProvider, storage_provider
from app.models.content import (
    AccessPolicy,
    ContentItem,
    DerivativeType,
    MediaAsset,
    MediaDerivative,
    MediaStatus,
    MediaType,
    VideoContent,
)


class RetryableMediaProcessingError(ConnectionError):
    """A transient failure persisted in a state the worker may safely replay."""


async def _derivative(
    db: AsyncSession,
    asset: MediaAsset,
    kind: DerivativeType,
    key: str,
    body: bytes,
    mime: str,
    width: int | None = None,
    height: int | None = None,
    duration: int | None = None,
    provider: StorageProvider | None = None,
    overwrite: bool = False,
) -> None:
    row = await db.scalar(
        select(MediaDerivative).where(
            MediaDerivative.media_asset_id == asset.id, MediaDerivative.derivative_type == kind
        )
    )
    if row and row.status is MediaStatus.ready and not overwrite:
        return
    (provider or storage_provider()).put(key, body, mime)
    if not row:
        row = MediaDerivative(
            media_asset_id=asset.id, derivative_type=kind, storage_key=key, mime_type=mime
        )
        db.add(row)
    row.status = MediaStatus.ready
    row.size_bytes = len(body)
    row.width, row.height, row.duration_seconds = width, height, duration


def _image_bytes(
    image: Image.Image, size: tuple[int, int], blur: bool = False
) -> tuple[bytes, int, int]:
    output = ImageOps.exif_transpose(image).convert("RGB")
    output.thumbnail(size)
    if blur:
        output = output.filter(ImageFilter.GaussianBlur(radius=18))
    data = io.BytesIO()
    output.save(data, format="WEBP", quality=84, method=6)
    return data.getvalue(), output.width, output.height


async def _process_image(
    db: AsyncSession, asset: MediaAsset, raw: bytes, provider: StorageProvider
) -> None:
    with Image.open(io.BytesIO(raw)) as image:
        image.verify()
    with Image.open(io.BytesIO(raw)) as image:
        normalized = ImageOps.exif_transpose(image)
        asset.width, asset.height = normalized.width, normalized.height
        for kind, size, blur in (
            (DerivativeType.thumbnail, (320, 320), False),
            (DerivativeType.display, (1600, 1600), False),
            (DerivativeType.blurred_preview, (800, 800), True),
        ):
            body, width, height = _image_bytes(image, size, blur)
            await _derivative(
                db,
                asset,
                kind,
                f"derivative/{asset.id}/{kind.value}.webp",
                body,
                "image/webp",
                width,
                height,
                provider=provider,
            )


def _run(*args: str) -> None:
    subprocess.run(args, check=True, capture_output=True, timeout=120)


async def _process_video(
    db: AsyncSession, asset: MediaAsset, raw: bytes, provider: StorageProvider
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / "source.mp4"
        source.write_bytes(raw)
        probe = subprocess.run(  # noqa: ASYNC221 - this runs only in the dedicated media worker
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        metadata = json.loads(probe.stdout)
        stream = next((item for item in metadata["streams"] if item["codec_type"] == "video"), None)
        if not stream:
            raise ValueError("Video stream is missing")
        asset.width, asset.height = int(stream["width"]), int(stream["height"])
        asset.duration_seconds = max(1, round(float(metadata["format"]["duration"])))
        poster = Path(directory) / "poster.jpg"
        playback = Path(directory) / "playback.mp4"
        _run(
            "ffmpeg",
            "-y",
            "-ss",
            "0",
            "-i",
            str(source),
            "-frames:v",
            "1",
            "-c:v",
            "mjpeg",
            str(poster),
        )
        _run(
            "ffmpeg",
            "-y",
            "-i",
            str(source),
            "-vf",
            "scale='min(1280,iw)':-2",
            "-c:v",
            "libx264",
            "-movflags",
            "+faststart",
            "-an",
            str(playback),
        )
        await _derivative(
            db,
            asset,
            DerivativeType.poster,
            f"derivative/{asset.id}/poster.jpg",
            poster.read_bytes(),
            "image/jpeg",
            asset.width,
            asset.height,
            provider=provider,
        )
        await _derivative(
            db,
            asset,
            DerivativeType.playback,
            f"derivative/{asset.id}/playback.mp4",
            playback.read_bytes(),
            "video/mp4",
            asset.width,
            asset.height,
            asset.duration_seconds,
            provider,
        )
        default_preview_duration = min(20, max(1, asset.duration_seconds - 1))
        await _render_video_preview(db, asset, source, 0, default_preview_duration, provider)


async def _render_video_preview(
    db: AsyncSession,
    asset: MediaAsset,
    source: Path,
    start_seconds: int,
    duration_seconds: int,
    provider: StorageProvider,
) -> None:
    body = _render_video_preview_bytes(source, start_seconds, duration_seconds)
    await _derivative(
        db,
        asset,
        DerivativeType.preview_clip,
        f"derivative/{asset.id}/preview.mp4",
        body,
        "video/mp4",
        asset.width,
        asset.height,
        duration_seconds,
        provider,
        overwrite=True,
    )


def _render_video_preview_bytes(source: Path, start_seconds: int, duration_seconds: int) -> bytes:
    preview = source.with_name("preview.mp4")
    _run(
        "ffmpeg",
        "-y",
        "-ss",
        str(start_seconds),
        "-t",
        str(duration_seconds),
        "-i",
        str(source),
        "-vf",
        "scale='min(854,iw)':-2",
        "-c:v",
        "libx264",
        "-movflags",
        "+faststart",
        "-an",
        str(preview),
    )
    return preview.read_bytes()


def _preview_snapshot_matches(
    video: VideoContent, start_seconds: int, duration_seconds: int
) -> bool:
    return (
        video.preview_start_seconds == start_seconds
        and video.preview_duration_seconds == duration_seconds
    )


async def _locked_video_for_snapshot(
    db: AsyncSession, content_id: UUID, start_seconds: int, duration_seconds: int
) -> VideoContent | None:
    video = await db.scalar(
        select(VideoContent)
        .where(VideoContent.content_id == content_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not video or not _preview_snapshot_matches(video, start_seconds, duration_seconds):
        return None
    return video


async def _set_preview_status_if_current(
    db: AsyncSession,
    content_id: UUID,
    asset_id: UUID,
    start_seconds: int,
    duration_seconds: int,
    status: MediaStatus,
) -> bool:
    video = await _locked_video_for_snapshot(db, content_id, start_seconds, duration_seconds)
    if not video or video.source_media_asset_id != asset_id:
        return False
    preview = await db.scalar(
        select(MediaDerivative)
        .where(
            MediaDerivative.media_asset_id == asset_id,
            MediaDerivative.derivative_type == DerivativeType.preview_clip,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not preview:
        return False
    preview.status = status
    return True


async def _publish_video_preview_if_current(
    db: AsyncSession,
    content_id: UUID,
    asset: MediaAsset,
    start_seconds: int,
    duration_seconds: int,
    body: bytes,
    provider: StorageProvider,
) -> bool:
    # Keep the lock order aligned with creator reconfiguration: selection first,
    # canonical derivative second. The storage write happens while both are held,
    # so a newer selection cannot commit between the CAS and publication.
    video = await _locked_video_for_snapshot(db, content_id, start_seconds, duration_seconds)
    if not video or video.source_media_asset_id != asset.id:
        return False
    preview = await db.scalar(
        select(MediaDerivative)
        .where(
            MediaDerivative.media_asset_id == asset.id,
            MediaDerivative.derivative_type == DerivativeType.preview_clip,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not preview:
        raise ValueError("Video preview derivative is missing")
    storage_key = (
        f"derivative/{asset.id}/preview-{content_id}-{start_seconds}-{duration_seconds}.mp4"
    )
    provider.put(storage_key, body, "video/mp4")
    preview.storage_key = storage_key
    preview.mime_type = "video/mp4"
    preview.status = MediaStatus.ready
    preview.size_bytes = len(body)
    preview.width = asset.width
    preview.height = asset.height
    preview.duration_seconds = duration_seconds
    return True


async def render_video_preview(
    db: AsyncSession,
    content_id: UUID,
    provider: StorageProvider | None = None,
    *,
    expected_start_seconds: int | None = None,
    expected_duration_seconds: int | None = None,
    retry_transient_failure: bool = False,
) -> bool:
    """Render one immutable selection snapshot; return False when it was superseded."""
    video = await db.scalar(select(VideoContent).where(VideoContent.content_id == content_id))
    if not video:
        raise ValueError("Video content not found")
    if (expected_start_seconds is None) != (expected_duration_seconds is None):
        raise ValueError("Video preview snapshot is incomplete")
    start_seconds = (
        video.preview_start_seconds if expected_start_seconds is None else expected_start_seconds
    )
    duration_seconds = (
        video.preview_duration_seconds
        if expected_duration_seconds is None
        else expected_duration_seconds
    )
    if not _preview_snapshot_matches(video, start_seconds, duration_seconds):
        return False
    asset = await db.get(MediaAsset, video.source_media_asset_id)
    if (
        not asset
        or asset.status is not MediaStatus.ready
        or asset.media_type is not MediaType.video
    ):
        raise ValueError("Video media is not ready")
    if asset.duration_seconds and start_seconds + duration_seconds > asset.duration_seconds:
        raise ValueError("Video preview must fit within the video duration")
    from app.content.service import validate_video_preview

    content = await db.get(ContentItem, video.content_id)
    if not content:
        raise ValueError("Video content not found")
    validate_video_preview(
        asset,
        start_seconds,
        duration_seconds,
        require_strict_teaser=content.access_policy is not AccessPolicy.free,
    )
    preview_row = await db.scalar(
        select(MediaDerivative).where(
            MediaDerivative.media_asset_id == asset.id,
            MediaDerivative.derivative_type == DerivativeType.preview_clip,
        )
    )
    if not preview_row:
        raise ValueError("Video preview derivative is missing")
    storage = provider or storage_provider()
    try:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.mp4"
            source.write_bytes(storage.get(asset.storage_key))
            body = _render_video_preview_bytes(
                source,
                start_seconds,
                duration_seconds,
            )
            return await _publish_video_preview_if_current(
                db,
                content_id,
                asset,
                start_seconds,
                duration_seconds,
                body,
                storage,
            )
    except ConnectionError as exc:
        is_current = await _set_preview_status_if_current(
            db,
            content_id,
            asset.id,
            start_seconds,
            duration_seconds,
            MediaStatus.queued if retry_transient_failure else MediaStatus.failed,
        )
        if not is_current:
            return False
        if retry_transient_failure:
            raise RetryableMediaProcessingError(
                "Transient video preview rendering failure"
            ) from exc
        raise
    except Exception:
        is_current = await _set_preview_status_if_current(
            db,
            content_id,
            asset.id,
            start_seconds,
            duration_seconds,
            MediaStatus.failed,
        )
        if not is_current:
            return False
        raise


async def process_media_asset(
    db: AsyncSession, asset_id: UUID, provider: StorageProvider | None = None
) -> MediaAsset:
    asset = await db.scalar(select(MediaAsset).where(MediaAsset.id == asset_id).with_for_update())
    if not asset:
        raise ValueError("Media asset not found")
    if asset.status is MediaStatus.ready:
        return asset
    if asset.status not in {MediaStatus.queued, MediaStatus.processing, MediaStatus.uploaded}:
        raise ValueError("Media asset is not ready for processing")
    if asset.processing_attempts >= get_settings().media_processing_max_attempts:
        raise ValueError("Media processing retry limit reached")
    storage = provider or storage_provider()
    asset.status = MediaStatus.processing
    asset.processing_attempts += 1
    try:
        raw = storage.get(asset.storage_key)
        asset.checksum_sha256 = checksum(raw)
        if asset.media_type is MediaType.image:
            await _process_image(db, asset, raw, storage)
        else:
            await _process_video(db, asset, raw, storage)
        asset.status, asset.processing_error = MediaStatus.ready, None
    except ConnectionError as exc:
        if asset.processing_attempts < get_settings().media_processing_max_attempts:
            asset.status = MediaStatus.queued
            asset.processing_error = "Transient media processing failure; retry queued"
            raise RetryableMediaProcessingError("Transient media processing failure") from exc
        asset.status = MediaStatus.failed
        asset.processing_error = "Media processing retry limit reached"
        raise
    except Exception:
        asset.status = MediaStatus.failed
        asset.processing_error = "Media processing failed"
        raise
    return asset
