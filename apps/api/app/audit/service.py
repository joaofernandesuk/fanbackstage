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
    forbidden = ("password", "token", "secret", "authorization", "cookie")
    safe_metadata = {
        key: value
        for key, value in (metadata or {}).items()
        if not any(word in key.lower() for word in forbidden)
    }
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
