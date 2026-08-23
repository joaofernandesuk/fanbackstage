"""Central report and case workflow; product domains remain enforcement owners."""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.models.content import MediaAsset
from app.models.creator import CreatorProfile
from app.models.featuring import FeatureBooking
from app.models.identity import User
from app.models.marketplace import MarketplaceListing, MarketplaceOrder
from app.models.messaging import Message
from app.models.social import FeedPost, PostComment
from app.models.streaming import LiveChatMessage, LiveRoom
from app.models.trust_safety import (
    ModerationCase,
    ModerationCaseStatus,
    ModerationEvidence,
    ModerationQueue,
    ModerationSeverity,
    ReportReason,
    ReportTargetType,
    TrustSafetyReport,
)

REPORT_DEDUPLICATION_WINDOW = timedelta(hours=24)
URGENT_REASONS = {ReportReason.underage_concern, ReportReason.non_consensual_content}


class TrustSafetyError(ValueError):
    pass


async def target_snapshot(db: AsyncSession, target_type: ReportTargetType, target_id: UUID) -> dict:
    """Resolve a reportable object server-side and retain only safe immutable context."""
    models: dict[ReportTargetType, tuple[type, str | None]] = {
        ReportTargetType.user: (User, "id"),
        ReportTargetType.creator: (CreatorProfile, "id"),
        ReportTargetType.post: (FeedPost, "id"),
        ReportTargetType.comment: (PostComment, "id"),
        ReportTargetType.media: (MediaAsset, "id"),
        ReportTargetType.message: (Message, "id"),
        ReportTargetType.live_room: (LiveRoom, "id"),
        ReportTargetType.live_chat_message: (LiveChatMessage, "id"),
        ReportTargetType.marketplace_listing: (MarketplaceListing, "id"),
        ReportTargetType.marketplace_order: (MarketplaceOrder, "id"),
        ReportTargetType.featured_placement: (FeatureBooking, "id"),
    }
    if target_type is ReportTargetType.referral_affiliate:
        from app.models.referral import AffiliatePartner

        row = await db.get(AffiliatePartner, target_id)
    else:
        model = models.get(target_type)
        row = await db.get(model[0], target_id) if model else None
    if not row:
        raise TrustSafetyError("Report target not found")
    snapshot = {"target_type": target_type.value, "target_id": str(target_id)}
    for field in ("public_id", "owner_creator_id", "creator_id", "sender_user_id", "status"):
        value = getattr(row, field, None)
        if value is not None:
            snapshot[field] = value.value if hasattr(value, "value") else str(value)
    return snapshot


def _case_public_id() -> str:
    return f"TS-{secrets.token_urlsafe(8).upper()}"


def queue_for(target_type: ReportTargetType, reason: ReportReason) -> ModerationQueue:
    if reason in URGENT_REASONS or reason is ReportReason.illegal_content:
        return ModerationQueue.urgent
    if reason is ReportReason.prohibited_marketplace_item or target_type in {
        ReportTargetType.marketplace_listing,
        ReportTargetType.marketplace_order,
    }:
        return ModerationQueue.marketplace
    if target_type in {ReportTargetType.live_room, ReportTargetType.live_chat_message}:
        return ModerationQueue.live
    return (
        ModerationQueue.content
        if target_type in {ReportTargetType.media, ReportTargetType.post}
        else ModerationQueue.general
    )


def severity_for(reason: ReportReason) -> ModerationSeverity:
    if reason is ReportReason.underage_concern:
        return ModerationSeverity.critical
    if reason in {
        ReportReason.non_consensual_content,
        ReportReason.illegal_content,
        ReportReason.threat_abuse,
    }:
        return ModerationSeverity.high
    return ModerationSeverity.medium


async def open_or_attach_report(
    db: AsyncSession,
    reporter: User,
    *,
    target_type: ReportTargetType,
    target_id: UUID,
    reason: ReportReason,
    details: str | None,
) -> tuple[TrustSafetyReport, ModerationCase, bool]:
    if details is not None and len(details.strip()) > 2000:
        raise TrustSafetyError("Report details are too long")
    snapshot = await target_snapshot(db, target_type, target_id)
    now = datetime.now(UTC)
    existing = await db.scalar(
        select(TrustSafetyReport)
        .where(
            TrustSafetyReport.reporter_user_id == reporter.id,
            TrustSafetyReport.target_type == target_type,
            TrustSafetyReport.target_id == target_id,
            TrustSafetyReport.reason == reason,
            TrustSafetyReport.created_at >= now - REPORT_DEDUPLICATION_WINDOW,
        )
        .order_by(TrustSafetyReport.created_at.desc())
    )
    case = await db.scalar(
        select(ModerationCase)
        .where(
            ModerationCase.primary_target_type == target_type,
            ModerationCase.primary_target_id == target_id,
            ModerationCase.status.not_in(
                [ModerationCaseStatus.resolved, ModerationCaseStatus.dismissed]
            ),
        )
        .order_by(ModerationCase.created_at.desc())
    )
    if case is None:
        severity = severity_for(reason)
        case = ModerationCase(
            public_id=_case_public_id(),
            primary_target_type=target_type,
            primary_target_id=target_id,
            severity=severity,
            priority=100 if severity is ModerationSeverity.critical else 0,
            queue=queue_for(target_type, reason),
            opened_at=now,
        )
        db.add(case)
        await db.flush()
    report = TrustSafetyReport(
        reporter_user_id=reporter.id,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        details=details.strip() if details else None,
        context_snapshot=snapshot,
        case_id=case.id,
        duplicate_of_report_id=existing.id if existing else None,
    )
    db.add(report)
    await db.flush()
    db.add(
        ModerationEvidence(
            case_id=case.id,
            source_type="report_context",
            source_id=report.id,
            snapshot=snapshot,
            sensitive=False,
            created_by_user_id=reporter.id,
        )
    )
    await record_event(
        db,
        "trust_safety.report_created",
        actor_user_id=reporter.id,
        target_type=target_type.value,
        target_id=str(target_id),
        metadata={"case_id": str(case.id), "reason": reason.value, "duplicate": bool(existing)},
    )
    return report, case, bool(existing)
