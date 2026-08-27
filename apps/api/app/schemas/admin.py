from pydantic import BaseModel

from app.models.content import MediaAudience


class MediaAudienceUpdate(BaseModel):
    audience: MediaAudience
