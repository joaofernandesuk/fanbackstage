from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.accounts.service import revoke_other_sessions, revoke_session
from app.api.deps import CurrentIdentity, Db
from app.api.routes.auth import user_response
from app.models.identity import UserSession
from app.permissions.policies import Permission, authorize
from app.schemas.auth import MessageResponse, SessionResponse, UserResponse

router = APIRouter(tags=["account"])


@router.get("/me", response_model=UserResponse)
async def me(identity: CurrentIdentity) -> UserResponse:
    user, _ = identity
    authorize(user, Permission.ACCOUNT_SELF_READ)
    return user_response(user)


@router.get("/sessions", response_model=list[SessionResponse])
async def sessions(identity: CurrentIdentity, db: Db) -> list[SessionResponse]:
    user, current = identity
    rows = (
        await db.scalars(
            select(UserSession).where(
                UserSession.user_id == user.id, UserSession.revoked_at.is_(None)
            )
        )
    ).all()
    return [
        SessionResponse(
            id=row.id,
            created_at=row.created_at,
            expires_at=row.expires_at,
            current=row.id == current.id,
            user_agent=row.user_agent,
        )
        for row in rows
    ]


@router.delete("/sessions/{session_id}", response_model=MessageResponse)
async def delete_session(
    session_id: str, identity: CurrentIdentity, db: Db, request: Request
) -> MessageResponse:
    user, _ = identity
    session = await db.scalar(
        select(UserSession).where(UserSession.id == session_id, UserSession.user_id == user.id)
    )
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    await revoke_session(db, session, request.state.correlation_id)
    await db.commit()
    return MessageResponse(message="Session revoked")


@router.delete("/sessions", response_model=MessageResponse)
async def delete_other_sessions(
    identity: CurrentIdentity, db: Db, request: Request
) -> MessageResponse:
    user, current = identity
    await revoke_other_sessions(db, user.id, current.id, request.state.correlation_id)
    await db.commit()
    return MessageResponse(message="Other sessions revoked")
