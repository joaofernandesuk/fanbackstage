from pydantic import BaseModel, Field

from app.models.content import MediaAudience, ModerationStatus


class MediaAudienceUpdate(BaseModel):
    audience: MediaAudience


class MediaModerationInput(BaseModel):
    status: ModerationStatus
    reason: str = Field(min_length=3, max_length=500)


class CreatorApplicationDecisionInput(BaseModel):
    """Human review rationale retained with the creator status transition audit."""

    reason: str | None = Field(default=None, min_length=3, max_length=255)
