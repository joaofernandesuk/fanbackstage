from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    EmailStr,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
)

from app.compliance.types import normalize_country_code

_email_adapter = TypeAdapter(EmailStr)
_demo_email_suffix = "@demo.fanbackstage.local"


def _account_email(value: str) -> str:
    """Normalize email input while admitting the documented local demo identities.

    Public registration remains an ``EmailStr`` and therefore cannot create
    special-use addresses. Login and account responses must also support the
    deterministic development users that are intentionally stored under the
    ``demo.fanbackstage.local`` namespace.
    """

    normalized = value.strip().lower()
    local_part = normalized.removesuffix(_demo_email_suffix)
    if normalized.endswith(_demo_email_suffix) and local_part and "@" not in local_part:
        return normalized
    try:
        return str(_email_adapter.validate_python(normalized))
    except ValidationError as exc:
        raise ValueError("Input should be a valid email address") from exc


AccountEmail = Annotated[
    str,
    Field(min_length=3, max_length=320),
    AfterValidator(_account_email),
]


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    adult_confirmed: Literal[True]
    country_code: str
    legal_version_ids: list[UUID] = Field(default_factory=list, max_length=20)

    @field_validator("country_code")
    @classmethod
    def valid_country(cls, value: str) -> str:
        normalized = normalize_country_code(value)
        assert normalized is not None
        return normalized

    @field_validator("legal_version_ids")
    @classmethod
    def unique_legal_versions(cls, values: list[UUID]) -> list[UUID]:
        if len(values) != len(set(values)):
            raise ValueError("Legal document version IDs must be unique")
        return values


class AdultAccessAcknowledgeRequest(BaseModel):
    adult_confirmed: Literal[True]


class AdultAccessStatusResponse(BaseModel):
    allowed: bool
    assurance: Literal["none", "self_attested"]
    source: Literal["none", "account", "cookie"]
    policy_version: str
    expires_at: datetime | None = None


class LoginRequest(BaseModel):
    email: AccountEmail
    password: str = Field(min_length=12, max_length=128)


class TokenRequest(BaseModel):
    token: str = Field(min_length=20, max_length=512)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResendVerificationRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(TokenRequest):
    new_password: str = Field(min_length=12, max_length=128)


class UserResponse(BaseModel):
    id: UUID
    email: AccountEmail
    email_verified: bool
    adult_attested: bool
    country_code: str | None
    roles: list[str]


class SessionResponse(BaseModel):
    id: UUID
    created_at: datetime
    expires_at: datetime
    current: bool
    user_agent: str | None


class MessageResponse(BaseModel):
    message: str
