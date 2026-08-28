import re

SAFE_DEMO_MEDIA_REFERENCE = re.compile(
    r"/demo/(?:[A-Za-z0-9_-]+/)*[A-Za-z0-9_-]+(?:\.[A-Za-z0-9]+)?"
)


def safe_public_profile_media_reference(value: str | None) -> str | None:
    """Project only reviewed local demo media until profiles own MediaAssets."""

    return value if value and SAFE_DEMO_MEDIA_REFERENCE.fullmatch(value) else None
