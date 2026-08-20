from uuid import UUID

from pydantic import BaseModel, Field

from app.models.content import AccessPolicy


class UploadIntent(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    mime_type: str = Field(min_length=1, max_length=127)


class UploadResponse(BaseModel):
    id: UUID
    status: str
    media_type: str | None = None
    upload_url: str | None = None


class GalleryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=5000)
    access_policy: AccessPolicy = AccessPolicy.free


class VideoCreate(GalleryCreate):
    media_asset_id: UUID


class GalleryItemCreate(BaseModel):
    media_asset_id: UUID
    is_preview: bool = False


class GalleryPreviewUpdate(BaseModel):
    preview_count: int = Field(ge=0, le=100)
    preview_asset_ids: list[UUID] = Field(default_factory=list, max_length=100)


class GalleryOrderUpdate(BaseModel):
    media_asset_ids: list[UUID] = Field(min_length=1, max_length=100)


class ContentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=5000)
    access_policy: AccessPolicy | None = None


class ContentPreview(BaseModel):
    derivative_id: UUID
    media_type: str
    delivery_path: str


class ContentResponse(BaseModel):
    id: UUID
    content_type: str
    title: str
    description: str | None
    status: str
    access_policy: str
    has_access: bool
    locked: bool
    previews: list[ContentPreview] = Field(default_factory=list)
