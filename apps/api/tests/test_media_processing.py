import io

import pytest
from PIL import Image
from sqlalchemy import select

from app.accounts import service as accounts
from app.creators import service as creators
from app.media.processing import process_media_asset
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
