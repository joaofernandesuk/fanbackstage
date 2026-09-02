from datetime import datetime
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


class AppealInput(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class AppealDecisionInput(AppealInput):
    outcome: str


class ConsentDecisionInput(BaseModel):
    approved: bool
    reason: str = Field(min_length=8, max_length=500)


class CreatorKycDecisionInput(BaseModel):
    action: str = Field(pattern="^(reject|request_reverification|leave_in_review)$")
    reason: str = Field(min_length=8, max_length=500)
    expected_status: str = Field(pattern="^needs_review$")


class ConsentReleaseInput(BaseModel):
    release_type: str
    participant_reference: str = Field(min_length=1, max_length=512)
    content_ids: list[UUID] = Field(min_length=1)
    effective_until: datetime | None = None
    evidence_reference: str | None = Field(default=None, max_length=512)
    supersedes_release_id: UUID | None = None


class EnforcementInput(BaseModel):
    action: str
    target_id: UUID
    reason: str = Field(min_length=1, max_length=500)
