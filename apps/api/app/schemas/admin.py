from pydantic import BaseModel, Field

from app.models.content import MediaAudience


class MediaAudienceUpdate(BaseModel):
    audience: MediaAudience


class CreatorApplicationDecisionInput(BaseModel):
    """Human review rationale retained with the creator status transition audit."""

    reason: str | None = Field(default=None, min_length=3, max_length=255)
