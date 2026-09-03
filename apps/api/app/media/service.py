import hashlib
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.core.config import get_settings
from app.creators.service import resolve_creator_compliance_eligibility
from app.media.storage import StorageProvider, storage_provider
from app.models.content import MediaAsset, MediaAudience, MediaStatus, MediaType, ModerationStatus
from app.models.creator import CreatorProfile, CreatorStatus
from app.models.identity import User
from app.permissions.policies import Permission, authorize

IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
VIDEO_TYPES = {"video/mp4", "video/webm"}


def media_type_for_mime(mime_type: str) -> MediaType:
    if mime_type in IMAGE_TYPES:
        return MediaType.image
    if mime_type in VIDEO_TYPES:
        return MediaType.video
    raise ValueError("Unsupported media type")


async def approved_creator(db: AsyncSession, user: User) -> CreatorProfile:
    profile = await db.scalar(select(CreatorProfile).where(CreatorProfile.user_id == user.id))
    if not profile or profile.status is not CreatorStatus.approved:
        raise PermissionError("An approved creator profile is required")
    eligibility = await resolve_creator_compliance_eligibility(db, profile=profile)
    if not eligibility.public_allowed:
        raise PermissionError(eligibility.reason)
    return profile


async def classify_audience(
    db: AsyncSession,
    actor: User,
    asset_id: UUID,
    audience: MediaAudience,
) -> tuple[MediaAsset, bool]:
    """Apply a moderator-owned audience decision once and audit actual changes."""

    authorize(actor, Permission.MODERATION_ACCESS)
    asset = await db.scalar(select(MediaAsset).where(MediaAsset.id == asset_id).with_for_update())
    if not asset:
        raise ValueError("Media asset not found")
    previous = asset.audience
    if previous is audience:
        return asset, False
    asset.audience = audience
    await record_event(
        db,
        "media.audience_classified",
        actor_user_id=actor.id,
        target_type="media_asset",
        target_id=str(asset.id),
        metadata={"previous": previous.value, "audience": audience.value},
    )
    return asset, True


async def moderate_asset(
    db: AsyncSession, actor: User, asset_id: UUID, status: ModerationStatus, reason: str
) -> MediaAsset:
    authorize(actor, Permission.MODERATION_ACCESS)
    if status not in {ModerationStatus.approved, ModerationStatus.rejected}:
        raise ValueError("Media moderation decision is invalid")
    asset = await db.scalar(select(MediaAsset).where(MediaAsset.id == asset_id).with_for_update())
    if asset is None or asset.status is not MediaStatus.ready:
        raise ValueError("Ready media asset not found")
    asset.moderation_status = status
    await record_event(
        db,
        f"media.{status.value}",
        actor_user_id=actor.id,
        target_type="media_asset",
        target_id=str(asset.id),
        metadata={"reason": reason},
    )
    return asset


async def begin_upload(
    db: AsyncSession,
    user: User,
    filename: str,
    mime_type: str,
    provider: StorageProvider | None = None,
) -> tuple[MediaAsset, str]:
    profile = await approved_creator(db, user)
    media_type = media_type_for_mime(mime_type)
    safe_filename = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:255] or "upload"
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=media_type,
        status=MediaStatus.pending_upload,
        storage_key=f"original/{profile.id}/{uuid4()}",
        original_filename=safe_filename,
        mime_type=mime_type,
    )
    db.add(asset)
    await db.flush()
    await record_event(
        db,
        "media.upload_initiated",
        actor_user_id=user.id,
        target_type="media_asset",
        target_id=str(asset.id),
    )
    url = (provider or storage_provider()).create_upload_url(
        asset.storage_key, mime_type, get_settings().media_url_ttl_seconds
    )
    return asset, url


async def finalize_upload(
    db: AsyncSession, user: User, asset_id: UUID, provider: StorageProvider | None = None
) -> MediaAsset:
    profile = await approved_creator(db, user)
    asset = await db.get(MediaAsset, asset_id)
    if not asset or asset.owner_creator_id != profile.id:
        raise PermissionError("Media asset not found")
    if asset.status is not MediaStatus.pending_upload:
        raise ValueError("Media upload cannot be finalized")
    size, detected_type = (provider or storage_provider()).head(asset.storage_key)
    max_bytes = (
        get_settings().media_max_image_bytes
        if asset.media_type is MediaType.image
        else get_settings().media_max_video_bytes
    )
    if size <= 0 or size > max_bytes or detected_type.split(";", 1)[0] != asset.mime_type:
        asset.status = MediaStatus.rejected
        asset.processing_error = "Upload did not satisfy media policy"
    else:
        asset.size_bytes = size
        asset.status = MediaStatus.queued
    await record_event(
        db,
        "media.upload_finalized",
        actor_user_id=user.id,
        target_type="media_asset",
        target_id=str(asset.id),
        metadata={"status": asset.status.value},
    )
    return asset


async def asset_for_owner(db: AsyncSession, user: User, asset_id: UUID) -> MediaAsset:
    profile = await approved_creator(db, user)
    asset = await db.get(MediaAsset, asset_id)
    if not asset or asset.owner_creator_id != profile.id or asset.deleted_at:
        raise PermissionError("Media asset not found")
    return asset


async def requeue_failed_upload(db: AsyncSession, user: User, asset_id: UUID) -> MediaAsset:
    asset = await asset_for_owner(db, user, asset_id)
    if asset.status is not MediaStatus.failed:
        raise ValueError("Only failed media can be requeued")
    if asset.processing_attempts >= get_settings().media_processing_max_attempts:
        raise ValueError("Media processing retry limit reached")
    asset.status = MediaStatus.queued
    asset.processing_error = None
    await record_event(
        db,
        "media.processing_requeued",
        actor_user_id=user.id,
        target_type="media_asset",
        target_id=str(asset.id),
        metadata={"attempt": asset.processing_attempts + 1},
    )
    return asset


def checksum(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()
