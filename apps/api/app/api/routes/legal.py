from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError

from app.api.deps import Db, OperatorRecoveryIdentity, OptionalIdentity, RawCurrentIdentity
from app.compliance.http import resolve_request_jurisdiction_with_evidence
from app.compliance.types import JurisdictionSignals
from app.legal import service
from app.models.identity import User
from app.models.legal import LegalDocumentStatus, LegalDocumentType
from app.permissions.policies import Permission, authorize
from app.schemas.legal import (
    LegalAcceptanceInput,
    LegalAcceptanceResponse,
    LegalDocumentCreate,
    LegalDocumentDetail,
    LegalDocumentPage,
    LegalDocumentResponse,
    LegalDraftUpdate,
    LegalRequirementResponse,
    LegalVersionCreate,
    SensitiveLegalAction,
    SiteSettingsInput,
    SiteSettingsResponse,
)

router = APIRouter(tags=["legal"])

CountryQuery = Annotated[str | None, Query(pattern=r"^[A-Za-z]{2}$")]
LanguageQuery = Annotated[
    str,
    Query(pattern=r"^[a-z]{2}(?:-[A-Z]{2})?$", max_length=16),
]
StatusQuery = Annotated[LegalDocumentStatus | None, Query(alias="status")]


def _raise_legal_error(exc: service.LegalError) -> None:
    code = (
        status.HTTP_404_NOT_FOUND if "not found" in str(exc).lower() else status.HTTP_409_CONFLICT
    )
    raise HTTPException(status_code=code, detail=str(exc)) from exc


async def _trusted_jurisdiction(db: Db, request: Request, user: User) -> str:
    """Read only the jurisdiction resolved by the shared compliance HTTP layer."""

    country_code = await resolve_request_jurisdiction_with_evidence(db, request, user=user)
    if country_code is None:
        raise HTTPException(status_code=409, detail="Jurisdiction could not be resolved")
    return country_code


async def _public_jurisdiction(
    db: Db,
    request: Request,
    user: User | None,
    selected_country: str | None,
) -> str:
    """Resolve browsing scope while treating a country selector as one conflictable signal."""

    signals = JurisdictionSignals(selected_country=selected_country) if selected_country else None
    country_code = await resolve_request_jurisdiction_with_evidence(
        db,
        request,
        user=user,
        signals=signals,
    )
    if country_code is None:
        raise HTTPException(
            status_code=409, detail="Jurisdiction signals conflict or are unavailable"
        )
    return country_code


async def _commit_or_invalid(db: Db) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Legal scope or jurisdiction is invalid",
        ) from exc


@router.get("/legal/documents", response_model=list[LegalDocumentResponse])
async def public_legal_documents(
    db: Db,
    identity: OptionalIdentity,
    request: Request,
    jurisdiction_code: CountryQuery = None,
    language: LanguageQuery = "en",
) -> list[LegalDocumentResponse]:
    user = identity[0] if identity else None
    return await service.active_documents(
        db,
        jurisdiction_code=await _public_jurisdiction(db, request, user, jurisdiction_code),
        language=language,
        audiences=await service.audiences_for_user(db, user),
    )


@router.get("/legal/documents/{slug}", response_model=LegalDocumentResponse)
async def public_legal_document(
    slug: str,
    db: Db,
    identity: OptionalIdentity,
    request: Request,
    jurisdiction_code: CountryQuery = None,
    language: LanguageQuery = "en",
) -> LegalDocumentResponse:
    user = identity[0] if identity else None
    document = await service.resolve_document(
        db,
        slug,
        jurisdiction_code=await _public_jurisdiction(db, request, user, jurisdiction_code),
        language=language,
        audiences=await service.audiences_for_user(db, user),
    )
    if not document:
        raise HTTPException(status_code=404, detail="Legal document not found")
    return document


@router.get(
    "/legal/registration-requirements",
    response_model=LegalRequirementResponse,
)
async def registration_legal_requirements(
    db: Db,
    request: Request,
    jurisdiction_code: CountryQuery = None,
    language: LanguageQuery = "en",
) -> LegalRequirementResponse:
    return LegalRequirementResponse(
        documents=await service.prospective_registration_requirements(
            db,
            jurisdiction_code=await _public_jurisdiction(db, request, None, jurisdiction_code),
            language=language,
        )
    )


@router.get("/legal/me/requirements", response_model=LegalRequirementResponse)
async def my_legal_requirements(
    identity: RawCurrentIdentity,
    db: Db,
    request: Request,
    language: LanguageQuery = "en",
) -> LegalRequirementResponse:
    return LegalRequirementResponse(
        documents=await service.required_documents(
            db,
            identity[0],
            jurisdiction_code=await _trusted_jurisdiction(db, request, identity[0]),
            language=language,
        )
    )


@router.get("/legal/me/acceptances", response_model=list[LegalAcceptanceResponse])
async def my_legal_acceptances(
    identity: RawCurrentIdentity, db: Db
) -> list[LegalAcceptanceResponse]:
    return await service.acceptance_history(db, identity[0].id)


@router.post("/legal/acceptances", response_model=list[LegalAcceptanceResponse])
async def accept_legal_documents(
    payload: LegalAcceptanceInput,
    identity: RawCurrentIdentity,
    db: Db,
    request: Request,
) -> list[LegalAcceptanceResponse]:
    try:
        accepted = await service.record_acceptances(
            db,
            identity[0],
            payload.version_ids,
            source=payload.source,
            jurisdiction_code=await _trusted_jurisdiction(db, request, identity[0]),
            correlation_id=request.state.correlation_id,
        )
        await _commit_or_invalid(db)
        return accepted
    except service.LegalError as exc:
        await db.rollback()
        _raise_legal_error(exc)


@router.get("/site-settings/public", response_model=SiteSettingsResponse)
async def public_site_settings(db: Db) -> SiteSettingsResponse:
    return await service.current_site_settings(db)


@router.get("/admin/legal/documents", response_model=LegalDocumentPage)
async def admin_legal_documents(
    identity: OperatorRecoveryIdentity,
    db: Db,
    search: str | None = Query(default=None, max_length=200),
    document_status: StatusQuery = None,
    document_type: LegalDocumentType | None = None,
    jurisdiction_code: CountryQuery = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> LegalDocumentPage:
    authorize(identity[0], Permission.LEGAL_DOCUMENT_EDIT)
    return await service.list_documents(
        db,
        search=search,
        status=document_status,
        document_type=document_type,
        jurisdiction_code=jurisdiction_code,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/admin/legal/documents",
    response_model=LegalDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_legal_document(
    payload: LegalDocumentCreate, identity: OperatorRecoveryIdentity, db: Db
) -> LegalDocumentResponse:
    authorize(identity[0], Permission.LEGAL_DOCUMENT_EDIT)
    try:
        document, version = await service.create_document(
            db,
            identity[0],
            payload.model_dump(mode="python"),
        )
        await _commit_or_invalid(db)
        return service.document_version_response(document, version)
    except service.LegalError as exc:
        await db.rollback()
        _raise_legal_error(exc)


@router.get("/admin/legal/documents/{document_id}", response_model=LegalDocumentDetail)
async def admin_legal_document_detail(
    document_id: UUID, identity: OperatorRecoveryIdentity, db: Db
) -> LegalDocumentDetail:
    authorize(identity[0], Permission.LEGAL_DOCUMENT_EDIT)
    try:
        return await service.document_detail(db, document_id)
    except service.LegalError as exc:
        _raise_legal_error(exc)


@router.post(
    "/admin/legal/documents/{document_id}/versions",
    response_model=LegalDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_legal_version(
    document_id: UUID,
    payload: LegalVersionCreate,
    identity: OperatorRecoveryIdentity,
    db: Db,
) -> LegalDocumentResponse:
    authorize(identity[0], Permission.LEGAL_DOCUMENT_EDIT)
    try:
        document, version = await service.create_version(
            db,
            identity[0],
            document_id,
            payload.model_dump(mode="python"),
        )
        await _commit_or_invalid(db)
        return service.document_version_response(document, version)
    except service.LegalError as exc:
        await db.rollback()
        _raise_legal_error(exc)


@router.get("/admin/legal/versions/{version_id}", response_model=LegalDocumentResponse)
async def admin_legal_version(
    version_id: UUID, identity: OperatorRecoveryIdentity, db: Db
) -> LegalDocumentResponse:
    authorize(identity[0], Permission.LEGAL_DOCUMENT_EDIT)
    try:
        document, version = await service.version_detail(db, version_id)
        return service.document_version_response(document, version)
    except service.LegalError as exc:
        _raise_legal_error(exc)


@router.patch("/admin/legal/versions/{version_id}", response_model=LegalDocumentResponse)
async def admin_update_legal_version(
    version_id: UUID,
    payload: LegalDraftUpdate,
    identity: OperatorRecoveryIdentity,
    db: Db,
) -> LegalDocumentResponse:
    authorize(identity[0], Permission.LEGAL_DOCUMENT_EDIT)
    try:
        values = payload.model_dump(mode="python", exclude_unset=True)
        document, version = await service.update_draft(
            db,
            identity[0],
            version_id,
            values,
        )
        await _commit_or_invalid(db)
        return service.document_version_response(document, version)
    except service.LegalError as exc:
        await db.rollback()
        _raise_legal_error(exc)


@router.post("/admin/legal/versions/{version_id}/publish", response_model=LegalDocumentResponse)
async def admin_publish_legal_version(
    version_id: UUID,
    payload: SensitiveLegalAction,
    identity: OperatorRecoveryIdentity,
    db: Db,
) -> LegalDocumentResponse:
    authorize(identity[0], Permission.LEGAL_DOCUMENT_PUBLISH)
    try:
        document, version = await service.publish_version(
            db,
            identity[0],
            version_id,
            reason=payload.reason,
        )
        # A newly-effective mandatory version invalidates existing connected
        # live authority. Queue durable controls before committing publication;
        # the LiveKit outbox worker performs provider I/O after commit.
        from app.streaming.service import reconcile_live_compliance_authority

        await reconcile_live_compliance_authority(db, commit_each=False)
        await _commit_or_invalid(db)
        return service.document_version_response(document, version)
    except service.LegalError as exc:
        await db.rollback()
        _raise_legal_error(exc)


@router.post("/admin/legal/versions/{version_id}/retire", response_model=LegalDocumentResponse)
async def admin_retire_legal_version(
    version_id: UUID,
    payload: SensitiveLegalAction,
    identity: OperatorRecoveryIdentity,
    db: Db,
) -> LegalDocumentResponse:
    authorize(identity[0], Permission.LEGAL_DOCUMENT_PUBLISH)
    try:
        document, version = await service.retire_version(
            db,
            identity[0],
            version_id,
            reason=payload.reason,
        )
        await _commit_or_invalid(db)
        return service.document_version_response(document, version)
    except service.LegalError as exc:
        await db.rollback()
        _raise_legal_error(exc)


@router.get("/admin/site-settings", response_model=SiteSettingsResponse)
async def admin_site_settings(identity: OperatorRecoveryIdentity, db: Db) -> SiteSettingsResponse:
    authorize(identity[0], Permission.SITE_SETTINGS_MANAGE)
    return await service.current_site_settings(db)


@router.put("/admin/site-settings", response_model=SiteSettingsResponse)
async def admin_update_site_settings(
    payload: SiteSettingsInput, identity: OperatorRecoveryIdentity, db: Db
) -> SiteSettingsResponse:
    authorize(identity[0], Permission.SITE_SETTINGS_MANAGE)
    try:
        settings = await service.update_site_settings(
            db,
            identity[0],
            payload.model_dump(mode="python"),
        )
        await _commit_or_invalid(db)
        return settings
    except service.LegalError as exc:
        await db.rollback()
        _raise_legal_error(exc)
