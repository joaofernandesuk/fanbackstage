from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.service import authenticate
from app.compliance.http import (
    resolve_request_compliance_decision,
    resolve_request_jurisdiction_with_evidence,
)
from app.core.config import get_settings
from app.db.session import get_db
from app.legal import service as legal_service
from app.models.compliance import ComplianceFeature
from app.models.identity import User, UserSession

Db = Annotated[AsyncSession, Depends(get_db)]


async def raw_current_identity(
    db: Db,
    request: Request,
) -> tuple[User, UserSession]:
    session_cookie = request.cookies.get(get_settings().session_cookie_name)
    identity = await authenticate(db, session_cookie)
    if not identity:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required"
        )
    return identity


RawCurrentIdentity = Annotated[tuple[User, UserSession], Depends(raw_current_identity)]


async def current_identity(
    request: Request,
    db: Db,
    identity: RawCurrentIdentity,
) -> tuple[User, UserSession]:
    user, _ = identity
    await enforce_current_platform_access(request, db, user)
    return identity


async def enforce_current_platform_access(request: Request, db: Db, user: User) -> None:
    """Apply the global authenticated jurisdiction, age, feature, and legal gate."""

    decision = await resolve_request_compliance_decision(
        db,
        request,
        user=user,
        feature=ComplianceFeature.platform_access,
    )
    if decision.allowed:
        return
    status_code = (
        428
        if decision.code == "LEGAL_ACCEPTANCE_REQUIRED"
        else status.HTTP_401_UNAUTHORIZED
        if decision.action == "LOGIN"
        else status.HTTP_403_FORBIDDEN
    )
    detail: dict[str, object] = {
        "code": decision.code,
        "action": decision.action,
        "message": decision.reason,
        "reason": decision.reason,
    }
    legal_versions = getattr(request.state, "legal_requirement_version_ids", ())
    if legal_versions:
        detail["version_ids"] = list(legal_versions)
    raise HTTPException(status_code=status_code, detail=detail)


async def enforce_current_legal_acceptance(request: Request, db: Db, user: User) -> None:
    """Fail closed when an authenticated account owes current legal acceptance."""

    if not await legal_service.has_effective_acceptance_requirements(db, user):
        return
    jurisdiction = await resolve_request_jurisdiction_with_evidence(db, request, user=user)
    if jurisdiction is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "JURISDICTION_UNRESOLVED",
                "action": "RESOLVE_COUNTRY",
                "message": "Jurisdiction must be resolved before continuing.",
                "reason": "Jurisdiction signals are unavailable or conflicting.",
            },
        )
    required = await legal_service.required_documents(
        db,
        user,
        jurisdiction_code=jurisdiction,
    )
    if required:
        raise HTTPException(
            status_code=428,
            detail={
                "code": "LEGAL_ACCEPTANCE_REQUIRED",
                "action": "ACCEPT_LEGAL",
                "message": "Current legal terms must be accepted before continuing.",
                "reason": "One or more current legal document versions require acceptance.",
                "version_ids": [str(document.version_id) for document in required],
            },
        )


async def operator_recovery_identity(
    request: Request,
    db: Db,
    identity: RawCurrentIdentity,
) -> tuple[User, UserSession]:
    """Allow audited policy recovery while still requiring current legal acceptance."""

    await enforce_current_legal_acceptance(request, db, identity[0])
    return identity


CurrentIdentity = Annotated[tuple[User, UserSession], Depends(current_identity)]
OperatorRecoveryIdentity = Annotated[tuple[User, UserSession], Depends(operator_recovery_identity)]


async def optional_identity(
    db: Db,
    request: Request,
) -> tuple[User, UserSession] | None:
    session_cookie = request.cookies.get(get_settings().session_cookie_name)
    return await authenticate(db, session_cookie)


OptionalIdentity = Annotated[tuple[User, UserSession] | None, Depends(optional_identity)]
