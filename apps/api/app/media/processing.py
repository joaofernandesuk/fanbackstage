import io
import json
import subprocess
import tempfile
from pathlib import Path
from uuid import UUID

from PIL import Image, ImageFilter, ImageOps
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.media.service import checksum
from app.media.storage import StorageProvider, storage_provider
from app.models.content import DerivativeType, MediaAsset, MediaDerivative, MediaStatus, MediaType


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
) -> None:
    row = await db.scalar(
        select(MediaDerivative).where(
            MediaDerivative.media_asset_id == asset.id, MediaDerivative.derivative_type == kind
        )
    )
    if row and row.status is MediaStatus.ready:
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
        poster = Path(directory) / "poster.webp"
        playback = Path(directory) / "playback.mp4"
        preview = Path(directory) / "preview.mp4"
        _run("ffmpeg", "-y", "-ss", "0", "-i", str(source), "-frames:v", "1", str(poster))
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
        _run(
            "ffmpeg",
            "-y",
            "-ss",
            "0",
            "-t",
            str(min(20, asset.duration_seconds)),
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
        await _derivative(
            db,
            asset,
            DerivativeType.poster,
            f"derivative/{asset.id}/poster.webp",
            poster.read_bytes(),
            "image/webp",
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
        await _derivative(
            db,
            asset,
            DerivativeType.preview_clip,
            f"derivative/{asset.id}/preview.mp4",
            preview.read_bytes(),
            "video/mp4",
            asset.width,
            asset.height,
            min(20, asset.duration_seconds),
            provider,
        )


async def process_media_asset(
    db: AsyncSession, asset_id: UUID, provider: StorageProvider | None = None
) -> MediaAsset:
    asset = await db.get(MediaAsset, asset_id)
    if not asset:
        raise ValueError("Media asset not found")
    if asset.status is MediaStatus.ready:
        return asset
    if asset.status not in {MediaStatus.queued, MediaStatus.processing, MediaStatus.uploaded}:
        raise ValueError("Media asset is not ready for processing")
    storage = provider or storage_provider()
    asset.status = MediaStatus.processing
    try:
        raw = storage.get(asset.storage_key)
        asset.checksum_sha256 = checksum(raw)
        if asset.media_type is MediaType.image:
            await _process_image(db, asset, raw, storage)
        else:
            await _process_video(db, asset, raw, storage)
        asset.status, asset.processing_error = MediaStatus.ready, None
    except Exception:
        asset.status, asset.processing_error = MediaStatus.failed, "Media processing failed"
        raise
    return asset
