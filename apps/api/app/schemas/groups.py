from uuid import UUID

from pydantic import BaseModel, Field


class GroupCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str = Field(pattern=r"^[a-z0-9-]{3,64}$")
    description: str | None = Field(default=None, max_length=5000)
    default_creator_basis_points: int = Field(ge=0, le=10_000)


class InvitationCreate(BaseModel):
    creator_id: UUID
    creator_basis_points: int | None = Field(default=None, ge=0, le=10_000)
    permissions: list[str] = Field(default_factory=list, max_length=20)


class ContractAmendment(BaseModel):
    creator_basis_points: int = Field(ge=0, le=10_000)


class AffiliationVisibility(BaseModel):
    visible: bool


class GroupResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    default_creator_basis_points: int


class MembershipResponse(BaseModel):
    id: UUID
    group_id: UUID
    creator_id: UUID
    status: str
    affiliation_public: bool


class ContractResponse(BaseModel):
    id: UUID
    membership_id: UUID
    version: int
    creator_basis_points: int
    group_basis_points: int
    status: str
