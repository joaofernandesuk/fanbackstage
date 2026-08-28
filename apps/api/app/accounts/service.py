import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from pwdlib import PasswordHash
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.adult_access import attest_account, current_policy_version
from app.audit.service import record_event
from app.core.config import get_settings
from app.models.identity import Role, SecurityToken, TokenPurpose, User, UserSession

password_hash = PasswordHash.recommended()
ROLES = ("viewer", "creator", "manager", "moderator", "admin", "super_admin")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


async def ensure_roles(db: AsyncSession) -> None:
    existing = set((await db.scalars(select(Role.name))).all())
    for name in ROLES:
        if name not in existing:
            db.add(Role(name=name, description=f"FanBackstage {name} role"))


async def assign_role(
    db: AsyncSession,
    user: User,
    role_name: str,
    actor_user_id: UUID | None,
    correlation_id: str | None,
) -> None:
    await ensure_roles(db)
    await db.flush()
    role = await db.scalar(select(Role).where(Role.name == role_name))
    if not role:
        raise ValueError("Unknown role")
    if role not in user.roles:
        user.roles.append(role)
        await record_event(
            db,
            "role.assigned",
            actor_user_id=actor_user_id,
            target_type="user",
            target_id=str(user.id),
            correlation_id=correlation_id,
            metadata={"role": role_name},
        )


async def register(
    db: AsyncSession,
    email: str,
    password: str,
    correlation_id: str | None,
    *,
    adult_confirmed: bool = False,
    country_code: str | None = None,
) -> tuple[User, str]:
    """Create an account, persisting self-attestation only from a trusted caller signal.

    Omitting ``adult_confirmed`` is deliberately safe: low-level callers create an
    unattested account which cannot perform adult-gated or paid actions.
    """
    normalized = email.strip().lower()
    if await db.scalar(select(User).where(User.email == normalized)):
        raise ValueError("An account with this email already exists")
    await ensure_roles(db)
    viewer = await db.scalar(select(Role).where(Role.name == "viewer"))
    user = User(
        email=normalized,
        password_hash=password_hash.hash(password),
        roles=[viewer],
        country_code=country_code,
    )
    if adult_confirmed:
        attest_account(user)
    db.add(user)
    await db.flush()
    token = await issue_security_token(db, user.id, TokenPurpose.email_verification)
    await record_event(
        db,
        "account.registered",
        actor_user_id=user.id,
        target_type="user",
        target_id=str(user.id),
        correlation_id=correlation_id,
        metadata={
            "adult_assurance": "self_attested" if adult_confirmed else "none",
            "country_code": country_code,
            **({"adult_attestation_version": current_policy_version()} if adult_confirmed else {}),
        },
    )
    return user, token


async def issue_security_token(db: AsyncSession, user_id: UUID, purpose: TokenPurpose) -> str:
    raw = secrets.token_urlsafe(32)
    db.add(
        SecurityToken(
            user_id=user_id,
            purpose=purpose,
            secret_hash=_digest(raw),
            expires_at=_now() + timedelta(hours=1),
        )
    )
    return raw


async def consume_security_token(db: AsyncSession, raw: str, purpose: TokenPurpose) -> User:
    token = await db.scalar(
        select(SecurityToken).where(
            SecurityToken.secret_hash == _digest(raw), SecurityToken.purpose == purpose
        )
    )
    if not token or token.consumed_at or token.expires_at <= _now():
        raise ValueError("Token is invalid or expired")
    token.consumed_at = _now()
    user = await db.get(User, token.user_id)
    assert user is not None
    return user


async def create_session(
    db: AsyncSession, user: User, correlation_id: str | None, user_agent: str | None
) -> str:
    raw = secrets.token_urlsafe(32)
    session = UserSession(
        user_id=user.id,
        secret_hash=_digest(raw),
        expires_at=_now() + timedelta(hours=get_settings().session_ttl_hours),
        user_agent=user_agent,
    )
    db.add(session)
    await record_event(
        db,
        "auth.login_succeeded",
        actor_user_id=user.id,
        target_type="session",
        target_id=str(session.id),
        correlation_id=correlation_id,
    )
    return raw


async def authenticate(db: AsyncSession, raw: str | None) -> tuple[User, UserSession] | None:
    if not raw:
        return None
    session = await db.scalar(select(UserSession).where(UserSession.secret_hash == _digest(raw)))
    if not session or session.revoked_at or session.expires_at <= _now():
        return None
    user = await db.scalar(select(User).where(User.id == session.user_id))
    if not user or not user.is_active or user.email_verified_at is None:
        return None
    await db.refresh(user, ["roles"])
    return user, session


async def revoke_session(
    db: AsyncSession, session: UserSession, correlation_id: str | None
) -> None:
    session.revoked_at = _now()
    await record_event(
        db,
        "auth.session_revoked",
        actor_user_id=session.user_id,
        target_type="session",
        target_id=str(session.id),
        correlation_id=correlation_id,
    )
    from app.streaming.service import evict_user_from_active_live

    await db.flush()
    await evict_user_from_active_live(
        db,
        session.user_id,
        reason="account_session_revoked",
        force=True,
    )


async def revoke_all_sessions(db: AsyncSession, user_id: UUID) -> None:
    await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    from app.streaming.service import evict_user_from_active_live

    await evict_user_from_active_live(
        db,
        user_id,
        reason="all_account_sessions_revoked",
        force=True,
    )


async def revoke_other_sessions(
    db: AsyncSession, user_id: UUID, current_session_id: UUID, correlation_id: str | None
) -> None:
    result = await db.execute(
        update(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.id != current_session_id,
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=_now())
    )
    if result.rowcount:
        await record_event(
            db,
            "auth.other_sessions_revoked",
            actor_user_id=user_id,
            target_type="session_collection",
            target_id=str(user_id),
            correlation_id=correlation_id,
            metadata={"revoked_count": result.rowcount},
        )
        from app.streaming.service import evict_user_from_active_live

        await evict_user_from_active_live(
            db,
            user_id,
            reason="other_account_sessions_revoked",
            force=True,
        )
