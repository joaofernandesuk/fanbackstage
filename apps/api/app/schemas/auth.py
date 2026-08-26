from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, BaseModel, EmailStr, Field, TypeAdapter, ValidationError

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


class LoginRequest(BaseModel):
    email: AccountEmail
    password: str = Field(min_length=12, max_length=128)


class TokenRequest(BaseModel):
    token: str = Field(min_length=20, max_length=512)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(TokenRequest):
    new_password: str = Field(min_length=12, max_length=128)


class UserResponse(BaseModel):
    id: UUID
    email: AccountEmail
    email_verified: bool
    roles: list[str]


class SessionResponse(BaseModel):
    id: UUID
    created_at: datetime
    expires_at: datetime
    current: bool
    user_agent: str | None


class MessageResponse(BaseModel):
    message: str
