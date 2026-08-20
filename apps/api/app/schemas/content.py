from uuid import UUID

from pydantic import BaseModel, Field, model_validator

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
    price_amount_minor: int | None = Field(default=None, gt=0, le=2_147_483_647)
    price_currency: str | None = Field(default=None, min_length=3, max_length=3)

    @model_validator(mode="after")
    def validate_ppv_price(self):
        has_price = self.price_amount_minor is not None or self.price_currency is not None
        if self.access_policy is AccessPolicy.ppv and (
            self.price_amount_minor is None or self.price_currency is None
        ):
            raise ValueError("PPV content requires a price and currency")
        if self.access_policy is not AccessPolicy.ppv and has_price:
            raise ValueError("Prices are only valid for PPV content")
        return self


class VideoCreate(GalleryCreate):
    media_asset_id: UUID
    preview_start_seconds: int = Field(default=0, ge=0)
    preview_duration_seconds: int = Field(default=20, ge=1, le=120)


class GalleryItemCreate(BaseModel):
    media_asset_id: UUID
    is_preview: bool = False


class GalleryPreviewUpdate(BaseModel):
    preview_count: int = Field(ge=0, le=100)
    preview_asset_ids: list[UUID] = Field(default_factory=list, max_length=100)


class GalleryCoverUpdate(BaseModel):
    media_asset_id: UUID


class GalleryOrderUpdate(BaseModel):
    media_asset_ids: list[UUID] = Field(min_length=1, max_length=100)


class ContentUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=5000)
    access_policy: AccessPolicy | None = None
    price_amount_minor: int | None = Field(default=None, gt=0, le=2_147_483_647)
    price_currency: str | None = Field(default=None, min_length=3, max_length=3)
    feed_announcement_override: bool | None = None


class VideoPreviewUpdate(BaseModel):
    preview_start_seconds: int = Field(ge=0)
    preview_duration_seconds: int = Field(ge=1, le=120)


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
    price_amount_minor: int | None = None
    price_currency: str | None = None
    previews: list[ContentPreview] = Field(default_factory=list)
