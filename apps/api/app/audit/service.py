from contextvars import ContextVar, Token
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditEvent

_request_metadata: ContextVar[tuple[str | None, str | None, str | None]] = ContextVar(
    "audit_request_metadata",
    default=(None, None, None),
)


def bind_request_metadata(
    ip_address: str | None,
    user_agent: str | None,
    correlation_id: str | None = None,
) -> Token[tuple[str | None, str | None, str | None]]:
    """Bind privacy-bounded request evidence for audit writes in this async context."""

    safe_ip = ip_address[:64] if ip_address else None
    safe_user_agent = user_agent[:512] if user_agent else None
    safe_correlation_id = correlation_id[:80] if correlation_id else None
    return _request_metadata.set((safe_ip, safe_user_agent, safe_correlation_id))


def reset_request_metadata(token: Token[tuple[str | None, str | None, str | None]]) -> None:
    _request_metadata.reset(token)


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
    ip_address, user_agent, request_correlation_id = _request_metadata.get()
    event = AuditEvent(
        event_type=event_type,
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=target_id,
        correlation_id=correlation_id or request_correlation_id,
        ip_address=ip_address,
        user_agent=user_agent,
        metadata_json=safe_metadata,
    )
    db.add(event)
    return event
