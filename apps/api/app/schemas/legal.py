from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models.legal import LegalAudience, LegalDocumentStatus, LegalDocumentType


def _normalise_jurisdiction(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip().upper()
    if len(normalized) != 2 or not normalized.isascii() or not normalized.isalpha():
        raise ValueError("Jurisdiction must be an ISO alpha-2 country code")
    return normalized


def _plain_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("Text cannot be blank")
    if any(ord(character) < 32 and character not in "\n\t" for character in normalized):
        raise ValueError("Text contains unsupported control characters")
    return normalized


class HeadingBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["heading"]
    level: Literal[2, 3, 4] = 2
    text: str = Field(min_length=1, max_length=300)

    _clean_text = field_validator("text")(_plain_text)


class ParagraphBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["paragraph"]
    text: str = Field(min_length=1, max_length=10_000)

    _clean_text = field_validator("text")(_plain_text)


class ListBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["list"]
    ordered: bool = False
    items: list[str] = Field(min_length=1, max_length=100)

    @field_validator("items")
    @classmethod
    def clean_items(cls, values: list[str]) -> list[str]:
        return [_plain_text(value) for value in values]


class CalloutBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["callout"]
    text: str = Field(min_length=1, max_length=2_000)

    _clean_text = field_validator("text")(_plain_text)


class LinkBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["link"]
    text: str = Field(min_length=1, max_length=300)
    href: str = Field(min_length=1, max_length=1_000)

    _clean_text = field_validator("text")(_plain_text)

    @field_validator("href")
    @classmethod
    def safe_link(cls, value: str) -> str:
        normalized = value.strip()
        if "\\" in normalized or any(ord(character) < 32 for character in normalized):
            raise ValueError("Legal links contain unsupported characters")
        parsed = urlparse(normalized)
        if normalized.startswith("/") and not normalized.startswith("//"):
            return normalized
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Legal links must be internal paths or HTTPS URLs")
        return normalized


LegalBodyBlock = Annotated[
    HeadingBlock | ParagraphBlock | ListBlock | CalloutBlock | LinkBlock,
    Field(discriminator="type"),
]


class LegalDocumentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: LegalDocumentType
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=96)
    jurisdiction_code: str | None = None
    language: str = Field(default="en", pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$", max_length=16)
    audience: LegalAudience = LegalAudience.all_users
    title: str = Field(min_length=1, max_length=200)
    body: list[LegalBodyBlock] = Field(min_length=1, max_length=500)
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    requires_acceptance: bool = False
    requires_legal_review: bool = True
    approved_for_publication: bool = False
    is_demo: bool = False

    _normalise_country = field_validator("jurisdiction_code")(_normalise_jurisdiction)
    _clean_title = field_validator("title")(_plain_text)

    @model_validator(mode="after")
    def valid_window(self) -> "LegalDocumentCreate":
        if (
            self.effective_from
            and self.effective_until
            and self.effective_until <= self.effective_from
        ):
            raise ValueError("Legal effective-until must follow effective-from")
        return self


class LegalVersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=200)
    body: list[LegalBodyBlock] = Field(min_length=1, max_length=500)
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    requires_acceptance: bool = False
    requires_legal_review: bool = True
    approved_for_publication: bool = False
    is_demo: bool = False

    _clean_title = field_validator("title")(_plain_text)

    @model_validator(mode="after")
    def valid_window(self) -> "LegalVersionCreate":
        if (
            self.effective_from
            and self.effective_until
            and self.effective_until <= self.effective_from
        ):
            raise ValueError("Legal effective-until must follow effective-from")
        return self


class LegalDraftUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=200)
    body: list[LegalBodyBlock] | None = Field(default=None, min_length=1, max_length=500)
    effective_from: datetime | None = None
    effective_until: datetime | None = None
    requires_acceptance: bool | None = None
    requires_legal_review: bool | None = None
    approved_for_publication: bool | None = None
    is_demo: bool | None = None

    @field_validator("title")
    @classmethod
    def clean_optional_title(cls, value: str | None) -> str | None:
        return _plain_text(value) if value is not None else None

    @model_validator(mode="after")
    def valid_window(self) -> "LegalDraftUpdate":
        fields = self.model_fields_set
        required_when_provided = {
            "title",
            "body",
            "requires_acceptance",
            "requires_legal_review",
            "approved_for_publication",
            "is_demo",
        }
        if any(getattr(self, field) is None for field in fields & required_when_provided):
            raise ValueError("Legal draft fields cannot be null when provided")
        if (
            {"effective_from", "effective_until"}.issubset(fields)
            and self.effective_from
            and self.effective_until
            and self.effective_until <= self.effective_from
        ):
            raise ValueError("Legal effective-until must follow effective-from")
        return self


class SensitiveLegalAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed: Literal[True]
    reason: str = Field(min_length=8, max_length=500)

    _clean_reason = field_validator("reason")(_plain_text)


class LegalDocumentResponse(BaseModel):
    document_id: UUID
    version_id: UUID
    document_type: LegalDocumentType
    title: str
    slug: str
    jurisdiction_code: str | None
    language: str
    audience: LegalAudience
    version: int
    status: LegalDocumentStatus
    body: list[LegalBodyBlock]
    effective_from: datetime | None
    effective_until: datetime | None
    requires_acceptance: bool
    requires_legal_review: bool
    approved_for_publication: bool
    is_demo: bool
    published_at: datetime | None


class LegalDocumentSummary(BaseModel):
    document_id: UUID
    version_id: UUID
    document_type: LegalDocumentType
    title: str
    slug: str
    jurisdiction_code: str | None
    language: str
    audience: LegalAudience
    version: int
    status: LegalDocumentStatus
    effective_from: datetime | None
    effective_until: datetime | None
    requires_acceptance: bool
    requires_legal_review: bool
    approved_for_publication: bool
    is_demo: bool
    created_at: datetime
    published_at: datetime | None


class LegalDocumentDetail(BaseModel):
    document_id: UUID
    document_type: LegalDocumentType
    slug: str
    jurisdiction_code: str | None
    language: str
    audience: LegalAudience
    versions: list[LegalDocumentSummary]


class LegalDocumentPage(BaseModel):
    items: list[LegalDocumentSummary]
    total: int
    limit: int
    offset: int


class LegalAcceptanceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version_ids: list[UUID] = Field(min_length=1, max_length=20)
    source: Literal["interstitial", "account"]

    @field_validator("version_ids")
    @classmethod
    def unique_versions(cls, values: list[UUID]) -> list[UUID]:
        if len(set(values)) != len(values):
            raise ValueError("Legal versions cannot be accepted twice in one request")
        return values


class LegalAcceptanceResponse(BaseModel):
    acceptance_id: UUID
    version_id: UUID
    document_type: LegalDocumentType
    title: str
    version: int
    jurisdiction_code: str | None
    source: str
    accepted_at: datetime


class LegalRequirementResponse(BaseModel):
    documents: list[LegalDocumentResponse]


class SiteSocialLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=1_000)

    _clean_label = field_validator("label")(_plain_text)

    @field_validator("url")
    @classmethod
    def safe_url(cls, value: str) -> str:
        normalized = value.strip()
        if "\\" in normalized or any(ord(character) < 32 for character in normalized):
            raise ValueError("Social links contain unsupported characters")
        parsed = urlparse(normalized)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError("Social links must use HTTPS")
        return normalized


class SiteSettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    support_email: EmailStr | None = None
    footer_text: str | None = Field(default=None, max_length=500)
    public_contact_text: str | None = Field(default=None, max_length=1_000)
    social_links: list[SiteSocialLink] = Field(default_factory=list, max_length=20)
    homepage_announcement: str | None = Field(default=None, max_length=2_000)
    maintenance_notice: str | None = Field(default=None, max_length=2_000)
    banner_level: Literal["info", "warning", "critical"] = "info"
    banner_starts_at: datetime | None = None
    banner_ends_at: datetime | None = None
    reason: str = Field(min_length=8, max_length=500)

    @field_validator(
        "footer_text", "public_contact_text", "homepage_announcement", "maintenance_notice"
    )
    @classmethod
    def clean_optional_text(cls, value: str | None) -> str | None:
        return _plain_text(value) if value is not None and value.strip() else None

    _clean_reason = field_validator("reason")(_plain_text)

    @model_validator(mode="after")
    def valid_window(self) -> "SiteSettingsInput":
        if (
            self.banner_starts_at
            and self.banner_ends_at
            and self.banner_ends_at <= self.banner_starts_at
        ):
            raise ValueError("Banner end must follow its start")
        return self


class SiteSettingsResponse(BaseModel):
    version: int
    support_email: str | None
    footer_text: str | None
    public_contact_text: str | None
    social_links: list[SiteSocialLink]
    homepage_announcement: str | None
    maintenance_notice: str | None
    banner_level: str
    banner_starts_at: datetime | None
    banner_ends_at: datetime | None
    banner_active: bool
    updated_at: datetime | None
