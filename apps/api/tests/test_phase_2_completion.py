import asyncio
import io
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select

from app.accounts import service as accounts
from app.api.routes.content import response as content_response
from app.content import service as content_service
from app.creators import service as creators
from app.media.processing import (
    RetryableMediaProcessingError,
    process_media_asset,
    render_video_preview,
)
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
    assert content_response(video, preview_duration_seconds=2).preview_duration_seconds == 2
    preview = await db_session.scalar(
        select(MediaDerivative).where(
            MediaDerivative.media_asset_id == asset.id,
            MediaDerivative.derivative_type == DerivativeType.preview_clip,
        )
    )
    assert preview
    assert preview.status is MediaStatus.queued
    transient_storage = TransientStorage(storage.objects)
    with pytest.raises(RetryableMediaProcessingError):
        await render_video_preview(
            db_session,
            video.id,
            transient_storage,
            retry_transient_failure=True,
        )
    assert preview.status is MediaStatus.queued
    assert await render_video_preview(db_session, video.id, transient_storage) is True
    assert preview.status is MediaStatus.ready
    assert preview.duration_seconds == 2
    exhausted_storage = TransientStorage(storage.objects)
    with pytest.raises(ConnectionError):
        await render_video_preview(db_session, video.id, exhausted_storage)
    assert preview.status is MediaStatus.failed
    assert await render_video_preview(db_session, video.id, storage) is True
    assert preview.status is MediaStatus.ready
    await content_service.submit_for_review(db_session, creator, video.id)
    assert video.status is ContentStatus.pending_review


@pytest.mark.asyncio
async def test_out_of_order_video_preview_job_cannot_replace_current_selection(
    db_session, tmp_path: Path
):
    creator, profile = await approved_creator(db_session, "video-preview-race@example.com")
    source = tmp_path / "race-source.mp4"
    await asyncio.to_thread(
        subprocess.run,
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=green:s=32x24:d=3",
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
        storage_key="original/preview-race",
        original_filename="race-source.mp4",
        mime_type="video/mp4",
    )
    db_session.add(asset)
    await db_session.flush()
    storage = MemoryStorage({asset.storage_key: (source.read_bytes(), "video/mp4")})
    await process_media_asset(db_session, asset.id, storage)
    content = await content_service.create_video(
        db_session,
        creator,
        "Preview race",
        None,
        asset.id,
        AccessPolicy.free,
        preview_start_seconds=1,
        preview_duration_seconds=2,
    )
    assert content.video
    preview = await db_session.scalar(
        select(MediaDerivative).where(
            MediaDerivative.media_asset_id == asset.id,
            MediaDerivative.derivative_type == DerivativeType.preview_clip,
        )
    )
    assert preview
    original_key = preview.storage_key

    class ReconfiguringStorage(MemoryStorage):
        def get(self, key: str) -> bytes:
            content.video.preview_start_seconds = 0
            content.video.preview_duration_seconds = 1
            preview.status = MediaStatus.queued
            return super().get(key)

    stale_storage = ReconfiguringStorage(storage.objects)
    applied = await render_video_preview(
        db_session,
        content.id,
        stale_storage,
        expected_start_seconds=1,
        expected_duration_seconds=2,
    )
    assert applied is False
    assert preview.status is MediaStatus.queued
    assert preview.storage_key == original_key

    assert (
        await render_video_preview(
            db_session,
            content.id,
            storage,
            expected_start_seconds=0,
            expected_duration_seconds=1,
        )
        is True
    )
    current_key = preview.storage_key
    current_object = storage.objects[current_key]
    assert current_key != original_key
    assert preview.status is MediaStatus.ready
    assert preview.duration_seconds == 1

    assert (
        await render_video_preview(
            db_session,
            content.id,
            storage,
            expected_start_seconds=1,
            expected_duration_seconds=2,
        )
        is False
    )
    assert preview.storage_key == current_key
    assert storage.objects[current_key] == current_object


def test_worker_reuses_one_event_loop_for_sequential_async_jobs():
    async def loop_id() -> int:
        return id(asyncio.get_running_loop())

    first = worker_tasks.run_async(loop_id())
    second = worker_tasks.run_async(loop_id())

    assert first == second


@pytest.mark.parametrize(
    "task,task_id",
    [
        (worker_tasks.process_media_asset, str(uuid4())),
        (worker_tasks.render_video_preview, str(uuid4())),
    ],
)
def test_media_worker_tasks_request_bounded_retry_for_transient_failures(
    monkeypatch, task, task_id
):
    retry_calls: list[dict[str, object]] = []

    class RetryRequested(Exception):
        pass

    def fail_transient(coroutine):
        coroutine.close()
        raise RetryableMediaProcessingError("temporary storage failure")

    def request_retry(**kwargs):
        retry_calls.append(kwargs)
        raise RetryRequested

    monkeypatch.setattr(worker_tasks, "run_async", fail_transient)
    monkeypatch.setattr(task, "retry", request_retry)

    with pytest.raises(RetryRequested):
        task.run(task_id)
    assert retry_calls[0]["max_retries"] == 2
    assert retry_calls[0]["countdown"] == 1
