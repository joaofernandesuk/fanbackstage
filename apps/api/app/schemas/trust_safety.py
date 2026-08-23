from uuid import UUID

from pydantic import BaseModel, Field


class TrustSafetyReportInput(BaseModel):
    target_type: str
    target_id: UUID
    reason: str
    details: str | None = Field(default=None, max_length=2000)
