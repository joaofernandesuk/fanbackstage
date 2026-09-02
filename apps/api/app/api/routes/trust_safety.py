from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from sqlalchemy import func, or_, select

from app.api.deps import CurrentIdentity, Db
from app.core.rate_limit import enforce_social_rate_limit
from app.models.audit import AuditEvent
from app.models.compliance import (
    PerformerAgeVerification,
    PerformerIdentity,
    PerformerIdentityVerification,
)
from app.models.content import ContentItem
from app.models.creator import CreatorProfile
from app.models.identity import User
from app.models.trust_safety import (
    AppealStatus,
    ConsentRelease,
    ConsentReleaseContent,
    ConsentReleaseStatus,
    ModerationAction,
    ModerationActionType,
    ModerationAppeal,
    ModerationCase,
    ModerationCaseNote,
    ModerationCaseStatus,
    ModerationEvidence,
    TrustSafetyReport,
)
from app.permissions.policies import Permission, authorize
from app.schemas.trust_safety import (
    AppealDecisionInput,
    AppealInput,
    CaseAssignmentInput,
    CaseNoteInput,
    ConsentDecisionInput,
    ConsentReleaseInput,
    EnforcementInput,
    TrustSafetyReportInput,
)
from app.trust_safety import service

router = APIRouter(prefix="/trust-safety", tags=["trust-safety"])


def _page(page: int, page_size: int) -> tuple[int, int]:
    return (page - 1) * page_size, page_size


async def _performer_verification_context(
    db: Db, creator_id: UUID, participant_reference: str
) -> dict:
    performer = await db.scalar(
        select(PerformerIdentity).where(
            PerformerIdentity.owner_creator_id == creator_id,
            PerformerIdentity.safe_reference == participant_reference,
        )
    )
    if performer is None:
        return {"registered": False, "identity_status": None, "age_status": None}
    identity_status = await db.scalar(
        select(PerformerIdentityVerification.status)
        .where(PerformerIdentityVerification.performer_id == performer.id)
        .order_by(PerformerIdentityVerification.created_at.desc())
        .limit(1)
    )
    age_status = await db.scalar(
        select(PerformerAgeVerification.status)
        .where(PerformerAgeVerification.performer_id == performer.id)
        .order_by(PerformerAgeVerification.created_at.desc())
        .limit(1)
    )
    return {
        "registered": True,
        "identity_status": identity_status.value if identity_status else None,
        "age_status": age_status.value if age_status else None,
    }


@router.get("/report-options")
async def report_options() -> dict:
    """Expose the canonical, server-owned reasons used by every report flow."""

    labels = {
        "harassment": "Harassment or bullying",
        "spam": "Spam",
        "impersonation": "Impersonation",
        "non_consensual_content": "Non-consensual content",
        "underage_concern": "Underage concern",
        "illegal_content": "Illegal content",
        "copyright": "Copyright concern",
        "scam_fraud": "Scam or fraud",
        "prohibited_marketplace_item": "Prohibited item",
        "threat_abuse": "Threat or abuse",
        "privacy": "Privacy concern",
        "other": "Other",
    }
    return {
        "reasons": [
            {"value": reason.value, "label": labels[reason.value]}
            for reason in service.ReportReason
        ]
    }


@router.post("/reports")
async def create_report(
    payload: TrustSafetyReportInput,
    request: Request,
    identity: CurrentIdentity,
    db: Db,
) -> dict:
    await enforce_social_rate_limit(request, str(identity[0].id), "trust_safety_report")
    try:
        report, case, duplicate = await service.open_or_attach_report(
            db,
            identity[0],
            target_type=service.ReportTargetType(payload.target_type),
            target_id=payload.target_id,
            reason=service.ReportReason(payload.reason),
            details=payload.details,
        )
        await db.commit()
        return {"report_id": str(report.id), "case_id": case.public_id, "duplicate": duplicate}
    except (ValueError, service.TrustSafetyError) as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.get("/cases")
async def list_cases(
    identity: CurrentIdentity, db: Db, status: ModerationCaseStatus | None = None
) -> list[dict]:
    authorize(identity[0], Permission.MODERATION_CASE_VIEW)
    query = select(ModerationCase)
    if status:
        query = query.where(ModerationCase.status == status)
    rows = (
        await db.scalars(query.order_by(ModerationCase.priority.desc(), ModerationCase.created_at))
    ).all()
    return [
        {
            "id": str(row.id),
            "public_id": row.public_id,
            "status": row.status.value,
            "severity": row.severity.value,
            "queue": row.queue.value,
            "assigned_moderator_id": str(row.assigned_moderator_id)
            if row.assigned_moderator_id
            else None,
        }
        for row in rows
    ]


@router.get("/cases/{case_id}")
async def case_detail(case_id: UUID, identity: CurrentIdentity, db: Db) -> dict:
    authorize(identity[0], Permission.MODERATION_CASE_VIEW)
    case = await db.get(ModerationCase, case_id)
    if not case:
        raise HTTPException(404, "Moderation case not found")
    reports = list(
        await db.scalars(select(TrustSafetyReport).where(TrustSafetyReport.case_id == case.id))
    )
    notes = list(
        await db.scalars(
            select(ModerationCaseNote)
            .where(ModerationCaseNote.case_id == case.id)
            .order_by(ModerationCaseNote.created_at)
        )
    )
    actions = list(
        await db.scalars(
            select(ModerationAction)
            .where(ModerationAction.case_id == case.id)
            .order_by(ModerationAction.created_at)
        )
    )
    evidence = list(
        await db.scalars(
            select(ModerationEvidence).where(
                ModerationEvidence.case_id == case.id, ModerationEvidence.sensitive.is_(False)
            )
        )
    )
    return {
        "id": str(case.id),
        "public_id": case.public_id,
        "status": case.status.value,
        "severity": case.severity.value,
        "priority": case.priority,
        "queue": case.queue.value,
        "target_type": case.primary_target_type.value,
        "target_id": str(case.primary_target_id),
        "report_count": len(reports),
        "notes": [
            {"id": str(note.id), "body": note.body, "created_at": note.created_at} for note in notes
        ],
        "actions": [
            {
                "id": str(action.id),
                "type": action.action_type.value,
                "reason": action.reason,
                "created_at": action.created_at,
                "reversal_action_id": str(action.reversal_action_id)
                if action.reversal_action_id
                else None,
            }
            for action in actions
        ],
        "safe_evidence": [
            {
                "id": str(item.id),
                "source_type": item.source_type,
                "safe_reference": item.safe_reference,
                "snapshot": item.snapshot,
            }
            for item in evidence
        ],
    }


@router.post("/cases/{case_id}/assign")
async def assign_case(
    case_id: UUID, payload: CaseAssignmentInput, identity: CurrentIdentity, db: Db
) -> dict:
    authorize(identity[0], Permission.MODERATION_CASE_TRIAGE)
    case = await db.get(ModerationCase, case_id)
    if not case:
        raise HTTPException(404, "Moderation case not found")
    try:
        case = await service.assign_case(db, case, identity[0], payload.moderator_id)
        await db.commit()
        return {"id": str(case.id), "status": case.status.value}
    except service.TrustSafetyError as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.post("/cases/{case_id}/notes")
async def add_note(
    case_id: UUID, payload: CaseNoteInput, identity: CurrentIdentity, db: Db
) -> dict:
    authorize(identity[0], Permission.MODERATION_CASE_TRIAGE)
    case = await db.get(ModerationCase, case_id)
    if not case:
        raise HTTPException(404, "Moderation case not found")
    try:
        note = await service.add_case_note(db, case, identity[0], payload.body)
        await db.commit()
        return {"id": str(note.id), "created_at": note.created_at}
    except service.TrustSafetyError as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.post("/cases/{case_id}/enforcement")
async def enforce_case(
    case_id: UUID, payload: EnforcementInput, identity: CurrentIdentity, db: Db
) -> dict:
    authorize(identity[0], Permission.MODERATION_ACTION)
    case = await db.get(ModerationCase, case_id)
    if not case:
        raise HTTPException(404, "Moderation case not found")
    handlers = {
        "contain_content": service.enforce_content_containment,
        "suspend_creator": service.enforce_creator_suspension,
        "suspend_marketplace": service.enforce_marketplace_suspension,
        "terminate_live": service.enforce_live_termination,
        "remove_live_participant": service.enforce_live_participant_removal,
        "disable_featuring": service.enforce_feature_disablement,
    }
    handler = handlers.get(payload.action)
    if not handler:
        raise HTTPException(400, "Unsupported enforcement action")
    try:
        action = await handler(db, case, identity[0], payload.target_id, payload.reason)
        await db.commit()
        return {"id": str(action.id), "type": action.action_type.value}
    except (PermissionError, ValueError, service.TrustSafetyError) as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.get("/cases/{case_id}/evidence/{evidence_id}")
async def evidence_access(
    case_id: UUID, evidence_id: UUID, identity: CurrentIdentity, db: Db
) -> dict:
    evidence = await db.scalar(
        select(ModerationEvidence).where(
            ModerationEvidence.id == evidence_id, ModerationEvidence.case_id == case_id
        )
    )
    if not evidence:
        raise HTTPException(404, "Evidence not found")
    authorize(
        identity[0],
        Permission.MODERATION_SENSITIVE_EVIDENCE
        if evidence.sensitive
        else Permission.MODERATION_CASE_VIEW,
    )
    from app.audit.service import record_event

    await record_event(
        db,
        "trust_safety.evidence_accessed",
        actor_user_id=identity[0].id,
        target_type="moderation_evidence",
        target_id=str(evidence.id),
    )
    await db.commit()
    return {
        "id": str(evidence.id),
        "source_type": evidence.source_type,
        "source_id": str(evidence.source_id) if evidence.source_id else None,
        # Raw sensitive evidence belongs in its protected store; it never crosses this API.
        "snapshot": None if evidence.sensitive else evidence.snapshot,
        "safe_reference": evidence.safe_reference,
        "sensitive": evidence.sensitive,
    }


@router.get("/appeals/mine/eligible")
async def eligible_appeals(identity: CurrentIdentity, db: Db) -> dict:
    now = datetime.now(UTC)
    rows = (
        await db.execute(
            select(ModerationAction, ContentItem, ModerationAppeal)
            .join(ContentItem, ContentItem.id == ModerationAction.target_id)
            .outerjoin(
                ModerationAppeal,
                ModerationAppeal.moderation_action_id == ModerationAction.id,
            )
            .where(
                ModerationAction.target_type == service.ReportTargetType.media,
                ContentItem.created_by_user_id == identity[0].id,
            )
            .order_by(ModerationAction.created_at.desc())
            .limit(100)
        )
    ).all()
    items = []
    for action, content, appeal in rows:
        deadline = action.created_at + service.APPEAL_WINDOW
        items.append(
            {
                "action_id": str(action.id),
                "action_type": action.action_type.value,
                "action_date": action.created_at,
                "content_title": content.title,
                "deadline": deadline,
                "eligible": now <= deadline and appeal is None,
                "appeal": (
                    {
                        "id": str(appeal.id),
                        "status": appeal.status.value,
                        "outcome": appeal.outcome,
                        "decided_at": appeal.decided_at,
                    }
                    if appeal
                    else None
                ),
            }
        )
    return {"items": items}


@router.get("/appeals/operations")
async def appeal_operations(
    identity: CurrentIdentity,
    db: Db,
    status: AppealStatus | None = None,
    action_type: ModerationActionType | None = None,
    search: str | None = None,
    assigned_to_me: bool = False,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict:
    authorize(identity[0], Permission.MODERATION_APPEAL_REVIEW)
    filters = []
    if status:
        filters.append(ModerationAppeal.status == status)
    if action_type:
        filters.append(ModerationAction.action_type == action_type)
    if assigned_to_me:
        filters.append(ModerationAppeal.reviewer_user_id == identity[0].id)
    if starts_at:
        filters.append(ModerationAppeal.created_at >= starts_at)
    if ends_at:
        filters.append(ModerationAppeal.created_at <= ends_at)
    if search:
        pattern = f"%{search.strip()}%"
        filters.append(or_(User.email.ilike(pattern), ContentItem.title.ilike(pattern)))
    joins = (
        select(ModerationAppeal, ModerationAction, ModerationCase, User, ContentItem)
        .join(ModerationAction, ModerationAction.id == ModerationAppeal.moderation_action_id)
        .join(ModerationCase, ModerationCase.id == ModerationAppeal.moderation_case_id)
        .join(User, User.id == ModerationAppeal.appellant_user_id)
        .join(ContentItem, ContentItem.id == ModerationAction.target_id)
    )
    count = await db.scalar(
        select(func.count())
        .select_from(ModerationAppeal)
        .join(ModerationAction, ModerationAction.id == ModerationAppeal.moderation_action_id)
        .join(User, User.id == ModerationAppeal.appellant_user_id)
        .join(ContentItem, ContentItem.id == ModerationAction.target_id)
        .where(*filters)
    )
    offset, limit = _page(page, page_size)
    rows = (
        await db.execute(
            joins.where(*filters)
            .order_by(ModerationAppeal.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    return {
        "items": [
            {
                "id": str(appeal.id),
                "status": appeal.status.value,
                "appellant": user.email,
                "reason": appeal.reason,
                "action_type": action.action_type.value,
                "action_date": action.created_at,
                "content_title": content.title,
                "case_public_id": case.public_id,
                "deadline": appeal.policy_deadline_at,
                "assigned_to_me": appeal.reviewer_user_id == identity[0].id,
                "reviewer_user_id": str(appeal.reviewer_user_id)
                if appeal.reviewer_user_id
                else None,
                "created_at": appeal.created_at,
                "decided_at": appeal.decided_at,
            }
            for appeal, action, case, user, content in rows
        ],
        "page": page,
        "page_size": page_size,
        "total": int(count or 0),
    }


@router.get("/appeals/operations/{appeal_id}")
async def appeal_operation_detail(appeal_id: UUID, identity: CurrentIdentity, db: Db) -> dict:
    authorize(identity[0], Permission.MODERATION_APPEAL_REVIEW)
    record = (
        await db.execute(
            select(ModerationAppeal, ModerationAction, ModerationCase, User, ContentItem)
            .join(ModerationAction, ModerationAction.id == ModerationAppeal.moderation_action_id)
            .join(ModerationCase, ModerationCase.id == ModerationAppeal.moderation_case_id)
            .join(User, User.id == ModerationAppeal.appellant_user_id)
            .join(ContentItem, ContentItem.id == ModerationAction.target_id)
            .where(ModerationAppeal.id == appeal_id)
        )
    ).one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Appeal not found")
    appeal, action, case, appellant, content = record
    audits = list(
        await db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.target_type == "moderation_appeal",
                AuditEvent.target_id == str(appeal.id),
            )
            .order_by(AuditEvent.created_at)
        )
    )
    return {
        "id": str(appeal.id),
        "status": appeal.status.value,
        "reason": appeal.reason,
        "appellant": appellant.email,
        "deadline": appeal.policy_deadline_at,
        "case": {
            "public_id": case.public_id,
            "severity": case.severity.value,
            "status": case.status.value,
        },
        "action": {
            "type": action.action_type.value,
            "date": action.created_at,
            "reason": action.reason,
            "target": content.title,
        },
        "reviewer_user_id": str(appeal.reviewer_user_id) if appeal.reviewer_user_id else None,
        "outcome": appeal.outcome,
        "decided_at": appeal.decided_at,
        "allowed_decisions": ["upheld", "overturned", "partially_overturned"]
        if appeal.status in {AppealStatus.submitted, AppealStatus.under_review}
        else [],
        "audit": [
            {
                "type": event.event_type,
                "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
                "created_at": event.created_at,
            }
            for event in audits
        ],
    }


@router.post("/appeals/{appeal_id}/claim")
async def claim_appeal(appeal_id: UUID, identity: CurrentIdentity, db: Db) -> dict:
    authorize(identity[0], Permission.MODERATION_APPEAL_REVIEW)
    try:
        appeal = await service.claim_appeal(db, appeal_id, identity[0])
        await db.commit()
        return {"id": str(appeal.id), "status": appeal.status.value}
    except service.TrustSafetyError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/actions/{action_id}/appeals")
async def submit_appeal(
    action_id: UUID, payload: AppealInput, identity: CurrentIdentity, db: Db
) -> dict:
    action = await db.get(ModerationAction, action_id)
    if not action:
        raise HTTPException(404, "Moderation action not found")
    try:
        appeal = await service.submit_appeal(db, action, identity[0], payload.reason)
        await db.commit()
        return {
            "id": str(appeal.id),
            "status": appeal.status.value,
            "deadline": appeal.policy_deadline_at,
        }
    except service.TrustSafetyError as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.post("/appeals/{appeal_id}/decision")
async def decide_appeal(
    appeal_id: UUID, payload: AppealDecisionInput, identity: CurrentIdentity, db: Db
) -> dict:
    authorize(identity[0], Permission.MODERATION_APPEAL_REVIEW)
    from app.models.trust_safety import AppealStatus, ModerationAppeal

    appeal = await db.get(ModerationAppeal, appeal_id)
    if not appeal:
        raise HTTPException(404, "Appeal not found")
    try:
        appeal = await service.decide_appeal(
            db, appeal, identity[0], AppealStatus(payload.outcome), payload.reason
        )
        await db.commit()
        return {"id": str(appeal.id), "status": appeal.status.value}
    except (ValueError, service.TrustSafetyError) as exc:
        await db.rollback()
        status_code = (
            409 if "changed" in str(exc).lower() or "assigned" in str(exc).lower() else 400
        )
        raise HTTPException(status_code, str(exc)) from exc


@router.get("/consent/context")
async def creator_consent_context(identity: CurrentIdentity, db: Db) -> dict:
    creator = await db.scalar(
        select(CreatorProfile).where(CreatorProfile.user_id == identity[0].id)
    )
    if creator is None:
        raise HTTPException(status_code=403, detail="Creator access required")
    contents = list(
        await db.scalars(
            select(ContentItem)
            .where(ContentItem.owner_creator_id == creator.id, ContentItem.deleted_at.is_(None))
            .order_by(ContentItem.created_at.desc())
            .limit(100)
        )
    )
    performers = list(
        await db.scalars(
            select(PerformerIdentity)
            .where(PerformerIdentity.owner_creator_id == creator.id)
            .order_by(PerformerIdentity.safe_reference)
            .limit(100)
        )
    )
    releases = list(
        await db.scalars(
            select(ConsentRelease)
            .where(ConsentRelease.owner_creator_id == creator.id)
            .order_by(ConsentRelease.created_at.desc())
            .limit(100)
        )
    )
    links = (
        (
            await db.execute(
                select(ConsentReleaseContent.consent_release_id, ContentItem.id, ContentItem.title)
                .join(ContentItem, ContentItem.id == ConsentReleaseContent.content_id)
                .where(ConsentReleaseContent.consent_release_id.in_([row.id for row in releases]))
            )
        ).all()
        if releases
        else []
    )
    linked: dict[UUID, list[dict]] = {}
    for release_id, content_id, title in links:
        linked.setdefault(release_id, []).append({"id": str(content_id), "title": title})
    return {
        "creator": {"display_name": creator.display_name, "username": creator.username},
        "contents": [
            {
                "id": str(row.id),
                "title": row.title,
                "type": row.content_type.value,
                "requires_consent": row.requires_verified_consent,
            }
            for row in contents
        ],
        "performers": [
            {"id": str(row.id), "label": row.safe_reference, "country_code": row.country_code}
            for row in performers
        ],
        "releases": [
            {
                "id": str(row.id),
                "status": row.status.value,
                "release_type": row.release_type.value,
                "participant": row.participant_reference,
                "contents": linked.get(row.id, []),
                "created_at": row.created_at,
                "effective_until": row.effective_until,
                "revoked_at": row.revoked_at,
            }
            for row in releases
        ],
    }


@router.post("/consent/releases")
async def create_own_consent_release(
    payload: ConsentReleaseInput, identity: CurrentIdentity, db: Db
) -> dict:
    creator = await db.scalar(
        select(CreatorProfile).where(CreatorProfile.user_id == identity[0].id)
    )
    if creator is None:
        raise HTTPException(status_code=403, detail="Creator access required")
    try:
        release = await service.submit_consent_release(
            db,
            creator,
            identity[0],
            service.ConsentReleaseType(payload.release_type),
            payload.participant_reference,
            payload.content_ids,
            payload.effective_until,
            payload.evidence_reference,
            payload.supersedes_release_id,
        )
        await db.commit()
        return {"id": str(release.id), "status": release.status.value}
    except (ValueError, service.TrustSafetyError) as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/consent/operations")
async def consent_operations(
    identity: CurrentIdentity,
    db: Db,
    status: ConsentReleaseStatus | None = None,
    search: str | None = None,
    starts_at: datetime | None = None,
    ends_at: datetime | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
) -> dict:
    authorize(identity[0], Permission.CONSENT_RELEASE_MANAGE)
    filters = []
    if status:
        filters.append(ConsentRelease.status == status)
    if starts_at:
        filters.append(ConsentRelease.created_at >= starts_at)
    if ends_at:
        filters.append(ConsentRelease.created_at <= ends_at)
    if search:
        pattern = f"%{search.strip()}%"
        filters.append(
            or_(
                CreatorProfile.username.ilike(pattern),
                CreatorProfile.display_name.ilike(pattern),
                ConsentRelease.participant_reference.ilike(pattern),
            )
        )
    total = await db.scalar(
        select(func.count())
        .select_from(ConsentRelease)
        .join(CreatorProfile, CreatorProfile.id == ConsentRelease.owner_creator_id)
        .where(*filters)
    )
    offset, limit = _page(page, page_size)
    rows = (
        await db.execute(
            select(ConsentRelease, CreatorProfile)
            .join(CreatorProfile, CreatorProfile.id == ConsentRelease.owner_creator_id)
            .where(*filters)
            .order_by(ConsentRelease.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
    ).all()
    items = []
    for release, creator in rows:
        content_titles = list(
            await db.scalars(
                select(ContentItem.title)
                .join(ConsentReleaseContent, ConsentReleaseContent.content_id == ContentItem.id)
                .where(ConsentReleaseContent.consent_release_id == release.id)
            )
        )
        items.append(
            {
                "id": str(release.id),
                "status": release.status.value,
                "release_type": release.release_type.value,
                "creator": creator.display_name or creator.username or "Creator",
                "participant": release.participant_reference,
                "content_titles": content_titles,
                "has_evidence": bool(release.evidence_reference),
                "performer_verification": await _performer_verification_context(
                    db, release.owner_creator_id, release.participant_reference
                ),
                "created_at": release.created_at,
            }
        )
    return {"items": items, "page": page, "page_size": page_size, "total": int(total or 0)}


@router.get("/consent/operations/{release_id}")
async def consent_operation_detail(release_id: UUID, identity: CurrentIdentity, db: Db) -> dict:
    authorize(identity[0], Permission.CONSENT_RELEASE_MANAGE)
    record = (
        await db.execute(
            select(ConsentRelease, CreatorProfile)
            .join(CreatorProfile, CreatorProfile.id == ConsentRelease.owner_creator_id)
            .where(ConsentRelease.id == release_id)
        )
    ).one_or_none()
    if record is None:
        raise HTTPException(status_code=404, detail="Consent release not found")
    release, creator = record
    contents = (
        await db.execute(
            select(ContentItem.id, ContentItem.title, ContentItem.content_type)
            .join(ConsentReleaseContent, ConsentReleaseContent.content_id == ContentItem.id)
            .where(ConsentReleaseContent.consent_release_id == release.id)
        )
    ).all()
    evidence_reference = None
    try:
        authorize(identity[0], Permission.MODERATION_SENSITIVE_EVIDENCE)
        evidence_reference = release.evidence_reference
    except HTTPException:
        pass
    audits = list(
        await db.scalars(
            select(AuditEvent)
            .where(AuditEvent.target_id == str(release.id))
            .order_by(AuditEvent.created_at)
        )
    )
    return {
        "id": str(release.id),
        "status": release.status.value,
        "creator": {
            "display_name": creator.display_name,
            "username": creator.username,
        },
        "release_type": release.release_type.value,
        "participant": release.participant_reference,
        "contents": [
            {"id": str(content_id), "title": title, "type": content_type.value}
            for content_id, title, content_type in contents
        ],
        "has_evidence": bool(release.evidence_reference),
        "evidence_reference": evidence_reference,
        "performer_verification": await _performer_verification_context(
            db, release.owner_creator_id, release.participant_reference
        ),
        "created_at": release.created_at,
        "effective_from": release.effective_from,
        "effective_until": release.effective_until,
        "verified_at": release.verified_at,
        "allowed_decisions": ["approve", "reject"]
        if release.status is ConsentReleaseStatus.pending
        else [],
        "audit": [
            {
                "type": event.event_type,
                "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
                "created_at": event.created_at,
            }
            for event in audits
        ],
    }


@router.post("/consent/operations/{release_id}/decision")
async def decide_consent_release(
    release_id: UUID,
    payload: ConsentDecisionInput,
    identity: CurrentIdentity,
    db: Db,
) -> dict:
    authorize(identity[0], Permission.CONSENT_RELEASE_MANAGE)
    release = await db.get(ConsentRelease, release_id)
    if release is None:
        raise HTTPException(status_code=404, detail="Consent release not found")
    try:
        release = await service.verify_consent_release(
            db, release, identity[0], payload.approved, payload.reason
        )
        await db.commit()
        return {"id": str(release.id), "status": release.status.value}
    except service.TrustSafetyError as exc:
        await db.rollback()
        status_code = 409 if "changed" in str(exc).lower() else 400
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc


@router.get("/creators/{creator_id}/consent-releases")
async def list_consent_releases(creator_id: UUID, identity: CurrentIdentity, db: Db) -> list[dict]:
    from app.models.creator import CreatorProfile

    creator = await db.get(CreatorProfile, creator_id)
    if not creator:
        raise HTTPException(404, "Creator not found")
    if not await service.can_manage_consent_releases(db, creator, identity[0]):
        raise HTTPException(403, "Consent-release management permission denied")
    releases = (
        await db.scalars(
            select(ConsentRelease)
            .where(ConsentRelease.owner_creator_id == creator.id)
            .order_by(ConsentRelease.created_at.desc())
        )
    ).all()
    return [
        {
            "id": str(release.id),
            "status": release.status.value,
            "release_type": release.release_type.value,
            "effective_until": release.effective_until,
            "revoked_at": release.revoked_at,
            "supersedes_release_id": str(release.supersedes_release_id)
            if release.supersedes_release_id
            else None,
        }
        for release in releases
    ]


@router.post("/creators/{creator_id}/consent-releases")
async def create_consent_release(
    creator_id: UUID, payload: ConsentReleaseInput, identity: CurrentIdentity, db: Db
) -> dict:
    from app.models.creator import CreatorProfile

    creator = await db.get(CreatorProfile, creator_id)
    if not creator:
        raise HTTPException(404, "Creator not found")
    try:
        release = await service.submit_consent_release(
            db,
            creator,
            identity[0],
            service.ConsentReleaseType(payload.release_type),
            payload.participant_reference,
            payload.content_ids,
            payload.effective_until,
            payload.evidence_reference,
            payload.supersedes_release_id,
        )
        await db.commit()
        return {"id": str(release.id), "status": release.status.value}
    except (ValueError, service.TrustSafetyError) as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.post("/consent-releases/{release_id}/verify")
async def verify_consent_release(
    release_id: UUID, approved: bool, identity: CurrentIdentity, db: Db
) -> dict:
    authorize(identity[0], Permission.CONSENT_RELEASE_MANAGE)
    release = await db.get(ConsentRelease, release_id)
    if not release:
        raise HTTPException(404, "Consent release not found")
    try:
        release = await service.verify_consent_release(db, release, identity[0], approved)
        await db.commit()
        return {"id": str(release.id), "status": release.status.value}
    except service.TrustSafetyError as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc


@router.post("/consent-releases/{release_id}/revoke")
async def revoke_consent_release(release_id: UUID, identity: CurrentIdentity, db: Db) -> dict:
    release = await db.get(ConsentRelease, release_id)
    if not release:
        raise HTTPException(404, "Consent release not found")
    try:
        release = await service.revoke_consent_release(db, release, identity[0])
        await db.commit()
        return {"id": str(release.id), "status": release.status.value}
    except service.TrustSafetyError as exc:
        await db.rollback()
        raise HTTPException(400, str(exc)) from exc
