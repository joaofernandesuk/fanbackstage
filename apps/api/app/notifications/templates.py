"""Small server-owned template registry; future locale variants retain stable keys/versions."""

import html
from dataclasses import dataclass


@dataclass(frozen=True)
class Template:
    key: str
    version: int = 1


def render(notification_type: str, payload: dict, classification: str) -> tuple[str, str, int]:
    """Render only escaped server-provided values, with a deterministic default locale."""
    title = html.escape(
        str(payload.get("subject") or payload.get("title") or "FanBackstage notification")
    )
    body = html.escape(str(payload.get("body") or "You have a FanBackstage notification."))
    footer = (
        "\n\nFanBackstage transactional notification."
        if classification == "transactional"
        else "\n\nFanBackstage marketing email. You can unsubscribe at any time."
    )
    return title, f"{body}{footer}", Template(notification_type).version
