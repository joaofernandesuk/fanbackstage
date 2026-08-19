from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.service import authenticate
from app.db.session import get_db
from app.models.identity import User, UserSession

Db = Annotated[AsyncSession, Depends(get_db)]


async def current_identity(
    request: Request,
    db: Db,
    session_cookie: str | None = Cookie(default=None, alias="fanbackstage_session"),
) -> tuple[User, UserSession]:
    identity = await authenticate(db, session_cookie)
    if not identity:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    return identity


CurrentIdentity = Annotated[tuple[User, UserSession], Depends(current_identity)]
