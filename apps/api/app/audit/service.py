from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditEvent


async def record_event(
    db: AsyncSession,
    event_type: str,
    *,
    actor_user_id: UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    correlation_id: str | None = None,
    metadata: dict | None = None,
) -> AuditEvent:
    forbidden = (
        "password",
        "token",
        "secret",
        "authorization",
        "cookie",
        "signature",
        "document",
        "evidence",
        "identity",
        "participant",
        "legal_name",
    )

    def scrub(value: object) -> object | None:
        if isinstance(value, dict):
            return {
                key: cleaned
                for key, nested in value.items()
                if not any(word in key.lower() for word in forbidden)
                if (cleaned := scrub(nested)) is not None
            }
        if isinstance(value, list):
            return [cleaned for nested in value if (cleaned := scrub(nested)) is not None]
        return value

    safe_metadata = scrub(metadata or {})
    assert isinstance(safe_metadata, dict)
    event = AuditEvent(
        event_type=event_type,
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
        correlation_id=correlation_id,
        metadata_json=safe_metadata,
    )
    db.add(event)
    return event
