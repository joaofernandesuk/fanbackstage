from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentIdentity, Db
from app.schemas.trust_safety import TrustSafetyReportInput
from app.trust_safety import service

router = APIRouter(prefix="/trust-safety", tags=["trust-safety"])


@router.post("/reports")
async def create_report(payload: TrustSafetyReportInput, identity: CurrentIdentity, db: Db) -> dict:
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
