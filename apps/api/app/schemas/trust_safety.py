from uuid import UUID

from pydantic import BaseModel, Field


class TrustSafetyReportInput(BaseModel):
    target_type: str
    target_id: UUID
    reason: str
    details: str | None = Field(default=None, max_length=2000)


class CaseAssignmentInput(BaseModel):
    moderator_id: UUID | None = None


class CaseNoteInput(BaseModel):
    body: str = Field(min_length=1, max_length=4000)
