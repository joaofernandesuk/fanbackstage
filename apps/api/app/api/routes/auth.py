from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select

from app.accounts import service
from app.api.deps import CurrentIdentity, Db
from app.core.config import get_settings
from app.core.rate_limit import enforce_auth_rate_limit
from app.integrations.email import email_provider
from app.models.identity import TokenPurpose, User
from app.referrals.service import ATTRIBUTION_COOKIE_NAME, snapshot_signup_attribution
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    MessageResponse,
    RegisterRequest,
    ResetPasswordRequest,
    TokenRequest,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


def user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        email_verified=user.email_verified_at is not None,
        roles=[role.name for role in user.roles],
    )


def set_cookie(response: Response, raw: str) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name,
        raw,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, request: Request, db: Db) -> UserResponse:
    await enforce_auth_rate_limit(request, str(payload.email).lower())
    try:
        user, token = await service.register(
            db, str(payload.email), payload.password, request.state.correlation_id
        )
        await snapshot_signup_attribution(db, user, request.cookies.get(ATTRIBUTION_COOKIE_NAME))
        await db.commit()
        await email_provider.send_security_link(user.email, "/verify-email", token)
        await db.refresh(user, ["roles"])
        return user_response(user)
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/login", response_model=UserResponse)
async def login(
    payload: LoginRequest, response: Response, request: Request, db: Db
) -> UserResponse:
    await enforce_auth_rate_limit(request, str(payload.email).lower())
    user = await db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if not user or not service.password_hash.verify(payload.password, user.password_hash):
        await service.record_event(
            db,
            "auth.login_failed",
            correlation_id=request.state.correlation_id,
            metadata={"email_hash": service._digest(str(payload.email).lower())},
        )
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password")
    await db.refresh(user, ["roles"])
    raw = await service.create_session(
        db, user, request.state.correlation_id, request.headers.get("user-agent")
    )
    await db.commit()
    set_cookie(response, raw)
    return user_response(user)


@router.post("/logout", response_model=MessageResponse)
async def logout(
    response: Response, request: Request, identity: CurrentIdentity, db: Db
) -> MessageResponse:
    _, session = identity
    await service.revoke_session(db, session, request.state.correlation_id)
    await db.commit()
    response.delete_cookie(get_settings().session_cookie_name, path="/")
    return MessageResponse(message="Logged out")


@router.post("/verify-email", response_model=MessageResponse)
async def verify_email(payload: TokenRequest, request: Request, db: Db) -> MessageResponse:
    await enforce_auth_rate_limit(request)
    try:
        user = await service.consume_security_token(
            db, payload.token, TokenPurpose.email_verification
        )
        user.email_verified_at = service._now()
        await service.record_event(
            db,
            "auth.email_verified",
            actor_user_id=user.id,
            correlation_id=request.state.correlation_id,
        )
        await db.commit()
        return MessageResponse(message="Email verified")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(
    payload: ForgotPasswordRequest, request: Request, db: Db
) -> MessageResponse:
    await enforce_auth_rate_limit(request, str(payload.email).lower())
    user = await db.scalar(select(User).where(User.email == str(payload.email).lower()))
    if user:
        token = await service.issue_security_token(db, user.id, TokenPurpose.password_reset)
        await service.record_event(
            db,
            "auth.password_reset_requested",
            actor_user_id=user.id,
            correlation_id=request.state.correlation_id,
        )
        await db.commit()
        await email_provider.send_security_link(user.email, "/reset-password", token)
    return MessageResponse(message="If an account exists, reset instructions have been sent")


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
    payload: ResetPasswordRequest, request: Request, db: Db
) -> MessageResponse:
    await enforce_auth_rate_limit(request)
    try:
        user = await service.consume_security_token(db, payload.token, TokenPurpose.password_reset)
        user.password_hash = service.password_hash.hash(payload.new_password)
        await service.revoke_all_sessions(db, user.id)
        await service.record_event(
            db,
            "auth.password_reset_completed",
            actor_user_id=user.id,
            correlation_id=request.state.correlation_id,
        )
        await db.commit()
        return MessageResponse(message="Password reset")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
