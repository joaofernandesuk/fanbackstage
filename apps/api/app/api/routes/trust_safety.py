from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.api.deps import CurrentIdentity, Db
from app.core.rate_limit import enforce_social_rate_limit
from app.models.trust_safety import (
    ConsentRelease,
    ModerationAction,
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
    ConsentReleaseInput,
    EnforcementInput,
    TrustSafetyReportInput,
)
from app.trust_safety import service

router = APIRouter(prefix="/trust-safety", tags=["trust-safety"])


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
        raise HTTPException(400, str(exc)) from exc


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
