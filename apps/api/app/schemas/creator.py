from uuid import UUID

from pydantic import BaseModel, Field, HttpUrl


class TaxonomyItem(BaseModel):
    id: UUID
    code: str
    label: str


class SocialLinkInput(BaseModel):
    label: str = Field(min_length=1, max_length=48)
    url: HttpUrl


class CreatorProfileUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=32)
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    bio: str | None = Field(default=None, max_length=2000)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    region: str | None = Field(default=None, max_length=80)
    city: str | None = Field(default=None, max_length=80)
    show_location: bool | None = None
    timezone: str | None = Field(default=None, max_length=64)
    language_codes: list[str] | None = Field(default=None, max_length=12)
    category_slugs: list[str] | None = Field(default=None, max_length=12)
    social_links: list[SocialLinkInput] | None = Field(default=None, max_length=12)
    is_public: bool | None = None


class CreatorSelfResponse(BaseModel):
    id: UUID
    username: str | None
    display_name: str | None
    bio: str | None
    country_code: str | None
    region: str | None
    city: str | None
    show_location: bool
    timezone: str | None
    status: str
    is_public: bool
    verification_status: str
    adult_verified: bool
    rejection_reason: str | None
    languages: list[TaxonomyItem]
    categories: list[TaxonomyItem]
    social_links: list[SocialLinkInput]


class PublicCreatorResponse(BaseModel):
    id: UUID
    username: str
    display_name: str
    bio: str | None
    avatar_reference: str | None
    cover_reference: str | None
    location: str | None
    timezone: str | None
    verified: bool
    languages: list[TaxonomyItem]
    categories: list[TaxonomyItem]
    social_links: list[SocialLinkInput]
