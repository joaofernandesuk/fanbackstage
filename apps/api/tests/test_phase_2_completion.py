import asyncio
import io
import subprocess
from pathlib import Path

import pytest
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from app.accounts import service as accounts
from app.content import service as content_service
from app.creators import service as creators
from app.media.processing import process_media_asset, render_video_preview
from app.media.service import requeue_failed_upload
from app.models.content import (
    AccessPolicy,
    ContentStatus,
    DerivativeType,
    MediaAsset,
    MediaDerivative,
    MediaStatus,
    MediaType,
    ModerationStatus,
)
from app.models.creator import CreatorStatus
from app.worker import tasks as worker_tasks


class MemoryStorage:
    def __init__(self, objects: dict[str, tuple[bytes, str]] | None = None) -> None:
        self.objects = objects or {}

    def get(self, key: str) -> bytes:
        return self.objects[key][0]

    def put(self, key: str, body: bytes, content_type: str) -> None:
        self.objects[key] = (body, content_type)


async def approved_creator(db, email: str):
    user, _ = await accounts.register(db, email, "strong-password-123", None)
    profile = await creators.get_or_create_profile(db, user)
    await creators.update_profile(
        db, profile, {"username": email.split("@")[0], "display_name": "Creator"}, user.id
    )
    await creators.submit(db, profile, user.id)
    await creators.development_verify(db, profile, True, user.id)
    await creators.set_status(db, profile, CreatorStatus.approved, user.id)
    return user, profile


async def ready_image(db, profile, key: str) -> MediaAsset:
    image = Image.new("RGB", (24, 12), "purple")
    data = io.BytesIO()
    image.save(data, format="PNG")
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        storage_key=key,
        original_filename="image.png",
        mime_type="image/png",
    )
    db.add(asset)
    await db.flush()
    return asset


@pytest.mark.asyncio
async def test_gallery_requires_review_and_creator_can_choose_cover(db_session):
    creator, profile = await approved_creator(db_session, "gallery-controls@example.com")
    first = await ready_image(db_session, profile, "original/gallery-first")
    second = await ready_image(db_session, profile, "original/gallery-second")
    gallery = await content_service.create_gallery(
        db_session, creator, "Gallery", None, AccessPolicy.free
    )
    await content_service.add_gallery_item(db_session, creator, gallery.id, first.id)
    await content_service.add_gallery_item(db_session, creator, gallery.id, second.id)
    await content_service.configure_gallery_cover(db_session, creator, gallery.id, second.id)
    await content_service.submit_for_review(db_session, creator, gallery.id)
    assert gallery.status is ContentStatus.pending_review
    assert gallery.moderation_status is ModerationStatus.queued
    assert gallery.gallery and gallery.gallery.cover_media_asset_id == second.id
    await content_service.approve(db_session, gallery, creator)
    assert gallery.status is ContentStatus.published
    assert gallery.moderation_status is ModerationStatus.approved


@pytest.mark.asyncio
async def test_failed_processing_is_bounded_and_owner_can_requeue(db_session):
    creator, profile = await approved_creator(db_session, "requeue@example.com")
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.queued,
        storage_key="original/invalid-image",
        original_filename="invalid.png",
        mime_type="image/png",
    )
    db_session.add(asset)
    await db_session.flush()
    storage = MemoryStorage({asset.storage_key: (b"not an image", "image/png")})
    with pytest.raises(UnidentifiedImageError):
        await process_media_asset(db_session, asset.id, storage)
    assert asset.status is MediaStatus.failed
    assert asset.processing_attempts == 1
    await requeue_failed_upload(db_session, creator, asset.id)
    assert asset.status is MediaStatus.queued
    assert asset.processing_error is None


@pytest.mark.asyncio
async def test_creator_selected_video_preview_is_rendered_before_review(db_session, tmp_path: Path):
    creator, profile = await approved_creator(db_session, "video-preview@example.com")
    source = tmp_path / "source.mp4"
    await asyncio.to_thread(
        subprocess.run,
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=32x24:d=3",
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
        storage_key="original/custom-preview",
        original_filename="source.mp4",
        mime_type="video/mp4",
    )
    db_session.add(asset)
    await db_session.flush()
    storage = MemoryStorage({asset.storage_key: (source.read_bytes(), "video/mp4")})
    await process_media_asset(db_session, asset.id, storage)
    video = await content_service.create_video(
        db_session,
        creator,
        "Video",
        None,
        asset.id,
        AccessPolicy.free,
        preview_start_seconds=1,
        preview_duration_seconds=2,
    )
    preview = await db_session.scalar(
        select(MediaDerivative).where(
            MediaDerivative.media_asset_id == asset.id,
            MediaDerivative.derivative_type == DerivativeType.preview_clip,
        )
    )
    assert preview
    assert preview.status is MediaStatus.queued
    await render_video_preview(db_session, video.id, storage)
    assert preview.status is MediaStatus.ready
    assert preview.duration_seconds == 2
    await content_service.submit_for_review(db_session, creator, video.id)
    assert video.status is ContentStatus.pending_review


def test_worker_reuses_one_event_loop_for_sequential_async_jobs():
    async def loop_id() -> int:
        return id(asyncio.get_running_loop())

    first = worker_tasks.run_async(loop_id())
    second = worker_tasks.run_async(loop_id())

    assert first == second
