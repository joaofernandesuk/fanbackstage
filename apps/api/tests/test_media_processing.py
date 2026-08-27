import asyncio
import io
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import select

from app.accounts import service as accounts
from app.content.service import validate_video_preview
from app.creators import service as creators
from app.media.processing import RetryableMediaProcessingError, process_media_asset
from app.media.service import asset_for_owner, begin_upload, finalize_upload
from app.models.content import DerivativeType, MediaAsset, MediaDerivative, MediaStatus, MediaType
from app.models.creator import CreatorStatus


class MemoryStorage:
    def __init__(self, objects: dict[str, tuple[bytes, str]] | None = None) -> None:
        self.objects = objects or {}

    def create_upload_url(self, key: str, content_type: str, expires_in: int) -> str:
        return f"memory://upload/{key}"

    def create_download_url(self, key: str, expires_in: int) -> str:
        return f"memory://download/{key}"

    def head(self, key: str) -> tuple[int, str]:
        body, content_type = self.objects[key]
        return len(body), content_type

    def get(self, key: str) -> bytes:
        return self.objects[key][0]

    def put(self, key: str, body: bytes, content_type: str) -> None:
        self.objects[key] = (body, content_type)

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


class TransientStorage(MemoryStorage):
    def __init__(self, objects: dict[str, tuple[bytes, str]], failures: int = 1) -> None:
        super().__init__(objects)
        self.failures = failures

    def get(self, key: str) -> bytes:
        if self.failures:
            self.failures -= 1
            raise ConnectionError("temporary object storage failure")
        return super().get(key)


async def approved_creator(db, email: str):
    user, _ = await accounts.register(db, email, "strong-password-123", None)
    profile = await creators.get_or_create_profile(db, user)
    await creators.update_profile(
        db,
        profile,
        {"username": email.split("@")[0], "display_name": "Media creator"},
        user.id,
    )
    await creators.submit(db, profile, user.id)
    await creators.development_verify(db, profile, True, user.id)
    await creators.set_status(db, profile, CreatorStatus.approved, user.id)
    return user, profile


@pytest.mark.asyncio
async def test_creator_upload_is_private_owned_and_finalized_server_side(db_session):
    creator, profile = await approved_creator(db_session, "media-owner@example.com")
    storage = MemoryStorage()
    asset, upload_url = await begin_upload(
        db_session, creator, "../../photo.png", "image/png", storage
    )
    assert upload_url.startswith("memory://upload/original/")
    assert asset.owner_creator_id == profile.id
    assert asset.storage_key.startswith("original/")
    assert "photo.png" == asset.original_filename
    storage.objects[asset.storage_key] = (b"not-an-image", "image/png")
    finalized = await finalize_upload(db_session, creator, asset.id, storage)
    assert finalized.status is MediaStatus.queued
    other_user, _ = await approved_creator(db_session, "other-owner@example.com")
    with pytest.raises(PermissionError):
        await asset_for_owner(db_session, other_user, asset.id)


@pytest.mark.asyncio
async def test_image_processing_generates_private_derivatives_idempotently(db_session):
    _, profile = await approved_creator(db_session, "image-processing@example.com")
    image = Image.new("RGB", (24, 12), "purple")
    encoded = io.BytesIO()
    image.save(encoded, format="PNG")
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.queued,
        storage_key="original/private-image",
        original_filename="image.png",
        mime_type="image/png",
    )
    db_session.add(asset)
    await db_session.flush()
    storage = MemoryStorage({asset.storage_key: (encoded.getvalue(), "image/png")})
    await process_media_asset(db_session, asset.id, storage)
    await db_session.flush()
    derivatives = (await db_session.scalars(select(MediaDerivative))).all()
    assert asset.status is MediaStatus.ready
    assert asset.width == 24 and asset.height == 12
    assert {row.derivative_type for row in derivatives} == {
        DerivativeType.thumbnail,
        DerivativeType.display,
        DerivativeType.blurred_preview,
    }
    assert all(row.storage_key.startswith(f"derivative/{asset.id}/") for row in derivatives)
    await process_media_asset(db_session, asset.id, storage)
    assert len((await db_session.scalars(select(MediaDerivative))).all()) == 3


@pytest.mark.asyncio
async def test_transient_asset_processing_stays_replayable_until_success(db_session):
    _, profile = await approved_creator(db_session, "image-retry@example.com")
    image = Image.new("RGB", (24, 12), "blue")
    encoded = io.BytesIO()
    image.save(encoded, format="PNG")
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.queued,
        storage_key="original/transient-image",
        original_filename="transient.png",
        mime_type="image/png",
    )
    db_session.add(asset)
    await db_session.flush()
    storage = TransientStorage({asset.storage_key: (encoded.getvalue(), "image/png")})

    with pytest.raises(RetryableMediaProcessingError):
        await process_media_asset(db_session, asset.id, storage)
    assert asset.status is MediaStatus.queued
    assert asset.processing_attempts == 1
    assert asset.processing_error == "Transient media processing failure; retry queued"

    await process_media_asset(db_session, asset.id, storage)
    assert asset.status is MediaStatus.ready
    assert asset.processing_attempts == 2
    assert asset.processing_error is None


@pytest.mark.asyncio
async def test_video_processing_generates_poster_preview_and_playback(db_session, tmp_path: Path):
    _, profile = await approved_creator(db_session, "video-processing@example.com")
    source = tmp_path / "fixture.mp4"
    await asyncio.to_thread(
        subprocess.run,
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=32x24:d=2",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
        capture_output=True,
    )
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.video,
        status=MediaStatus.queued,
        storage_key="original/private-video",
        original_filename="fixture.mp4",
        mime_type="video/mp4",
    )
    db_session.add(asset)
    await db_session.flush()
    storage = MemoryStorage({asset.storage_key: (source.read_bytes(), "video/mp4")})
    original_object = storage.objects[asset.storage_key]
    await process_media_asset(db_session, asset.id, storage)
    await db_session.flush()
    derivatives = (await db_session.scalars(select(MediaDerivative))).all()
    assert asset.status is MediaStatus.ready
    assert (asset.width, asset.height, asset.duration_seconds) == (32, 24, 2)
    assert {row.derivative_type for row in derivatives} == {
        DerivativeType.poster,
        DerivativeType.preview_clip,
        DerivativeType.playback,
    }
    assert all(row.storage_key.startswith(f"derivative/{asset.id}/") for row in derivatives)
    by_type = {row.derivative_type: row for row in derivatives}
    assert by_type[DerivativeType.preview_clip].duration_seconds == 1
    assert (
        by_type[DerivativeType.preview_clip].duration_seconds
        < by_type[DerivativeType.playback].duration_seconds
    )
    assert storage.objects[asset.storage_key] == original_object


def test_non_free_video_preview_must_end_before_protected_source():
    asset = MediaAsset(duration_seconds=8)
    validate_video_preview(asset, 0, 8)
    with pytest.raises(ValueError, match="must end before"):
        validate_video_preview(asset, 0, 8, require_strict_teaser=True)
    with pytest.raises(ValueError, match="must end before"):
        validate_video_preview(asset, 1, 7, require_strict_teaser=True)
    validate_video_preview(asset, 0, 2, require_strict_teaser=True)
