"""Central report and case workflow; product domains remain enforcement owners."""

import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.compliance.policy import effective_policy_for_country
from app.compliance.types import ASSURANCE_STRENGTH, normalize_country_code
from app.models.compliance import (
    AgeVerificationStatus,
    PerformerAgeVerification,
    PerformerIdentity,
    PerformerIdentityStatus,
    PerformerIdentityVerification,
    VerifiedContentPerformer,
)
from app.models.content import ContentItem, MediaAsset
from app.models.creator import CreatorProfile
from app.models.featuring import FeatureBooking, FeatureIneligibilityReason
from app.models.identity import User
from app.models.marketplace import MarketplaceListing, MarketplaceOrder
from app.models.messaging import Message
from app.models.social import FeedPost, PostComment
from app.models.streaming import (
    LiveChatMessage,
    LiveCommerceCharge,
    LiveCommerceKind,
    LiveParticipant,
    LiveRoom,
)
from app.models.trust_safety import (
    AppealStatus,
    ConsentRelease,
    ConsentReleaseContent,
    ConsentReleaseStatus,
    ConsentReleaseType,
    ModerationAction,
    ModerationActionType,
    ModerationAppeal,
    ModerationCase,
    ModerationCaseNote,
    ModerationCaseStatus,
    ModerationEvidence,
    ModerationQueue,
    ModerationSeverity,
    ReportReason,
    ReportTargetType,
    TrustSafetyReport,
)
from app.notifications.service import emit_transactional

REPORT_DEDUPLICATION_WINDOW = timedelta(hours=24)
APPEAL_WINDOW = timedelta(days=30)
URGENT_REASONS = {ReportReason.underage_concern, ReportReason.non_consensual_content}


class TrustSafetyError(ValueError):
    pass


async def can_manage_consent_releases(
    db: AsyncSession, creator: CreatorProfile, actor: User
) -> bool:
    if creator.user_id == actor.id:
        return True
    from app.groups.service import has_delegated_permission
    from app.models.groups import GroupPermission

    return await has_delegated_permission(
        db, actor.id, creator.id, GroupPermission.manage_consent_releases
    )


async def target_snapshot(
    db: AsyncSession,
    reporter: User,
    target_type: ReportTargetType,
    target_id: UUID,
) -> dict:
    """Resolve a reportable object server-side and retain only safe immutable context."""
    models: dict[ReportTargetType, tuple[type, str | None]] = {
        ReportTargetType.user: (User, "id"),
        ReportTargetType.creator: (CreatorProfile, "id"),
        ReportTargetType.post: (FeedPost, "id"),
        ReportTargetType.comment: (PostComment, "id"),
        ReportTargetType.message: (Message, "id"),
        ReportTargetType.live_room: (LiveRoom, "id"),
        ReportTargetType.live_chat_message: (LiveChatMessage, "id"),
        ReportTargetType.live_paid_request: (LiveCommerceCharge, "id"),
        ReportTargetType.marketplace_listing: (MarketplaceListing, "id"),
        ReportTargetType.marketplace_order: (MarketplaceOrder, "id"),
        ReportTargetType.featured_placement: (FeatureBooking, "id"),
    }
    if target_type is ReportTargetType.referral_affiliate:
        from app.models.referral import AffiliatePartner

        row = await db.get(AffiliatePartner, target_id)
    else:
        if target_type is ReportTargetType.media:
            row = await db.get(ContentItem, target_id) or await db.get(MediaAsset, target_id)
        else:
            model = models.get(target_type)
            row = await db.get(model[0], target_id) if model else None
    if not row:
        raise TrustSafetyError("Report target not found")

    # Reporting must not become an object-existence oracle for content that the
    # reporter could not otherwise access. Public and entitled content use the
    # same domain resolvers as their serving surfaces; raw assets remain
    # reportable by their creator owner, while viewers report the owning
    # content/message/listing rather than its private storage object.
    if isinstance(row, ContentItem):
        from app.content.access import can_access_content

        if not await can_access_content(db, row, reporter):
            raise TrustSafetyError("Report target not found")
    elif isinstance(row, MediaAsset):
        owner_user_id = await db.scalar(
            select(CreatorProfile.user_id).where(CreatorProfile.id == row.owner_creator_id)
        )
        if owner_user_id != reporter.id:
            raise TrustSafetyError("Report target not found")
    elif isinstance(row, FeedPost):
        from app.social.service import can_access_post

        if not await can_access_post(db, row, reporter):
            raise TrustSafetyError("Report target not found")
    elif isinstance(row, PostComment):
        from app.social.service import can_access_post

        post = await db.get(FeedPost, row.post_id)
        if (
            post is None
            or row.hidden_at is not None
            or row.deleted_at is not None
            or not await can_access_post(db, post, reporter)
        ):
            raise TrustSafetyError("Report target not found")
    elif isinstance(row, Message):
        from app.messaging.service import assert_participant
        from app.models.messaging import Conversation

        conversation = await db.get(Conversation, row.conversation_id)
        if conversation is None:
            raise TrustSafetyError("Report target not found")
        try:
            await assert_participant(db, conversation, reporter)
        except PermissionError as exc:
            raise TrustSafetyError("Report target not found") from exc
    elif isinstance(row, MarketplaceOrder):
        seller_user_id = await db.scalar(
            select(CreatorProfile.user_id).where(CreatorProfile.id == row.seller_creator_id)
        )
        if reporter.id not in {row.buyer_user_id, seller_user_id}:
            raise TrustSafetyError("Report target not found")
    elif isinstance(row, LiveRoom):
        participant = await db.scalar(
            select(LiveParticipant.id).where(
                LiveParticipant.live_room_id == row.id,
                LiveParticipant.user_id == reporter.id,
            )
        )
        if participant is None:
            raise TrustSafetyError("Report target not found")
    elif isinstance(row, LiveChatMessage):
        participant = await db.scalar(
            select(LiveParticipant.id).where(
                LiveParticipant.live_room_id == row.live_room_id,
                LiveParticipant.user_id == reporter.id,
            )
        )
        if participant is None:
            raise TrustSafetyError("Report target not found")
    elif isinstance(row, LiveCommerceCharge):
        creator_user_id = await db.scalar(
            select(CreatorProfile.user_id).where(CreatorProfile.id == row.creator_id)
        )
        if (
            row.kind is not LiveCommerceKind.paid_request
            or reporter.id not in {row.buyer_user_id, creator_user_id}
        ):
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
    if target_type in {
        ReportTargetType.live_room,
        ReportTargetType.live_chat_message,
        ReportTargetType.live_paid_request,
    }:
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
    snapshot = await target_snapshot(db, reporter, target_type, target_id)
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
    severity = severity_for(reason)
    queue = queue_for(target_type, reason)
    if case is None:
        case = ModerationCase(
            public_id=_case_public_id(),
            primary_target_type=target_type,
            primary_target_id=target_id,
            status=(
                ModerationCaseStatus.action_required
                if queue is ModerationQueue.urgent
                else ModerationCaseStatus.open
            ),
            severity=severity,
            priority=100 if severity is ModerationSeverity.critical else 0,
            queue=queue,
            opened_at=now,
        )
        db.add(case)
        await db.flush()
    else:
        # A later urgent signal must elevate an already-open lower-severity
        # case without silently rewinding active moderator investigation.
        severity_rank = {
            ModerationSeverity.low: 0,
            ModerationSeverity.medium: 1,
            ModerationSeverity.high: 2,
            ModerationSeverity.critical: 3,
        }
        if severity_rank[severity] > severity_rank[case.severity]:
            case.severity = severity
        if severity is ModerationSeverity.critical:
            case.priority = max(case.priority, 100)
        if queue is ModerationQueue.urgent:
            case.queue = ModerationQueue.urgent
            if case.status in {ModerationCaseStatus.open, ModerationCaseStatus.triage}:
                case.status = ModerationCaseStatus.action_required
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


async def assign_case(
    db: AsyncSession, case: ModerationCase, actor: User, moderator_id: UUID | None
) -> ModerationCase:
    if moderator_id is not None and not await db.get(User, moderator_id):
        raise TrustSafetyError("Moderator not found")
    case.assigned_moderator_id = moderator_id
    if case.status is ModerationCaseStatus.open:
        case.status = ModerationCaseStatus.triage
    await record_event(
        db,
        "trust_safety.case_assigned",
        actor_user_id=actor.id,
        target_type="moderation_case",
        target_id=str(case.id),
        metadata={"assigned": str(moderator_id) if moderator_id else None},
    )
    return case


async def add_case_note(
    db: AsyncSession, case: ModerationCase, actor: User, body: str
) -> ModerationCaseNote:
    if not body.strip() or len(body.strip()) > 4000:
        raise TrustSafetyError("Invalid moderator note")
    note = ModerationCaseNote(case_id=case.id, author_user_id=actor.id, body=body.strip())
    db.add(note)
    await db.flush()
    await record_event(
        db,
        "trust_safety.case_note_added",
        actor_user_id=actor.id,
        target_type="moderation_case",
        target_id=str(case.id),
    )
    return note


async def enforce_content_containment(
    db: AsyncSession, case: ModerationCase, actor: User, content_id: UUID, reason: str
) -> ModerationAction:
    """Record once, then invoke content's authoritative moderation command."""
    existing = await db.scalar(
        select(ModerationAction).where(
            ModerationAction.case_id == case.id,
            ModerationAction.action_type == ModerationActionType.temporary_containment,
            ModerationAction.target_type == ReportTargetType.media,
            ModerationAction.target_id == content_id,
        )
    )
    if existing:
        return existing
    from app.content import service as content_service

    await content_service.apply_moderation_containment(db, actor, content_id, reason)
    action = ModerationAction(
        case_id=case.id,
        action_type=ModerationActionType.temporary_containment,
        target_type=ReportTargetType.media,
        target_id=content_id,
        actor_user_id=actor.id,
        reason=reason,
    )
    db.add(action)
    await db.flush()
    return action


async def enforce_creator_suspension(
    db: AsyncSession, case: ModerationCase, actor: User, creator_id: UUID, reason: str
) -> ModerationAction:
    """Suspend through the creator lifecycle service; never rewrite financial history."""
    existing = await db.scalar(
        select(ModerationAction).where(
            ModerationAction.case_id == case.id,
            ModerationAction.action_type == ModerationActionType.creator_suspend,
            ModerationAction.target_type == ReportTargetType.creator,
            ModerationAction.target_id == creator_id,
        )
    )
    if existing:
        return existing
    from app.creators import service as creator_service
    from app.models.creator import CreatorStatus

    creator = await db.get(CreatorProfile, creator_id)
    if not creator:
        raise TrustSafetyError("Creator not found")
    if creator.status is not CreatorStatus.suspended:
        await creator_service.set_status(db, creator, CreatorStatus.suspended, actor.id, reason)
    action = ModerationAction(
        case_id=case.id,
        action_type=ModerationActionType.creator_suspend,
        target_type=ReportTargetType.creator,
        target_id=creator.id,
        actor_user_id=actor.id,
        reason=reason,
    )
    db.add(action)
    await db.flush()
    return action


async def reverse_creator_suspension(
    db: AsyncSession, action: ModerationAction, actor: User, reason: str
) -> ModerationAction:
    """Restore only a suspended creator through its lifecycle transition."""
    if action.action_type is not ModerationActionType.creator_suspend:
        raise TrustSafetyError("Only creator suspension can be restored")
    if action.reversal_action_id:
        restored = await db.get(ModerationAction, action.reversal_action_id)
        assert restored
        return restored
    from app.creators import service as creator_service
    from app.models.creator import CreatorStatus

    creator = await db.get(CreatorProfile, action.target_id)
    if not creator:
        raise TrustSafetyError("Creator not found")
    if creator.status is CreatorStatus.suspended:
        await creator_service.set_status(db, creator, CreatorStatus.approved, actor.id, reason)
        creator.is_public = True
    restoration = ModerationAction(
        case_id=action.case_id,
        action_type=ModerationActionType.creator_unsuspend,
        target_type=ReportTargetType.creator,
        target_id=creator.id,
        actor_user_id=actor.id,
        reason=reason,
    )
    db.add(restoration)
    await db.flush()
    action.reversal_action_id, action.reversed_at = restoration.id, datetime.now(UTC)
    return restoration


async def enforce_marketplace_suspension(
    db: AsyncSession, case: ModerationCase, actor: User, creator_id: UUID, reason: str
) -> ModerationAction:
    existing = await db.scalar(
        select(ModerationAction).where(
            ModerationAction.case_id == case.id,
            ModerationAction.action_type == ModerationActionType.marketplace_selling_suspend,
            ModerationAction.target_id == creator_id,
        )
    )
    if existing:
        return existing
    from app.marketplace import service as marketplace_service

    await marketplace_service.set_marketplace_suspension(db, actor, creator_id, True, reason)
    action = ModerationAction(
        case_id=case.id,
        action_type=ModerationActionType.marketplace_selling_suspend,
        target_type=ReportTargetType.creator,
        target_id=creator_id,
        actor_user_id=actor.id,
        reason=reason,
    )
    db.add(action)
    await db.flush()
    return action


async def enforce_live_termination(
    db: AsyncSession, case: ModerationCase, actor: User, room_id: UUID, reason: str
) -> ModerationAction:
    existing = await db.scalar(
        select(ModerationAction).where(
            ModerationAction.case_id == case.id,
            ModerationAction.action_type == ModerationActionType.live_terminate,
            ModerationAction.target_id == room_id,
        )
    )
    if existing:
        return existing
    from app.streaming import service as streaming_service

    await streaming_service.terminate_live_for_moderation(db, actor, room_id, reason)
    action = ModerationAction(
        case_id=case.id,
        action_type=ModerationActionType.live_terminate,
        target_type=ReportTargetType.live_room,
        target_id=room_id,
        actor_user_id=actor.id,
        reason=reason,
    )
    db.add(action)
    await db.flush()
    return action


async def enforce_live_participant_removal(
    db: AsyncSession,
    case: ModerationCase,
    actor: User,
    user_id: UUID,
    reason: str,
) -> ModerationAction:
    if case.primary_target_type is not ReportTargetType.live_room:
        raise TrustSafetyError("Participant removal requires a Live room case")
    existing = await db.scalar(
        select(ModerationAction).where(
            ModerationAction.case_id == case.id,
            ModerationAction.action_type == ModerationActionType.live_participant_remove,
            ModerationAction.target_type == ReportTargetType.user,
            ModerationAction.target_id == user_id,
        )
    )
    if existing:
        return existing
    from app.streaming import service as streaming_service

    await streaming_service.remove_live_participant_for_moderation(
        db, actor, case.primary_target_id, user_id, reason
    )
    action = ModerationAction(
        case_id=case.id,
        action_type=ModerationActionType.live_participant_remove,
        target_type=ReportTargetType.user,
        target_id=user_id,
        actor_user_id=actor.id,
        reason=reason,
    )
    db.add(action)
    await db.flush()
    return action


async def enforce_feature_disablement(
    db: AsyncSession, case: ModerationCase, actor: User, booking_id: UUID, reason: str
) -> ModerationAction:
    existing = await db.scalar(
        select(ModerationAction).where(
            ModerationAction.case_id == case.id,
            ModerationAction.action_type == ModerationActionType.featured_placement_disable,
            ModerationAction.target_id == booking_id,
        )
    )
    if existing:
        return existing
    booking = await db.get(FeatureBooking, booking_id)
    if not booking:
        raise TrustSafetyError("Feature booking not found")
    from app.featuring import service as featuring_service

    await featuring_service.terminate_ineligible(
        db, booking, FeatureIneligibilityReason.moderation_ineligible
    )
    action = ModerationAction(
        case_id=case.id,
        action_type=ModerationActionType.featured_placement_disable,
        target_type=ReportTargetType.featured_placement,
        target_id=booking.id,
        actor_user_id=actor.id,
        reason=reason,
    )
    db.add(action)
    await db.flush()
    return action


async def restore_feature_eligibility(
    db: AsyncSession, action: ModerationAction, actor: User, reason: str
) -> ModerationAction:
    """Record overturned eligibility only; a disabled booking is permanently terminal."""
    if action.action_type is not ModerationActionType.featured_placement_disable:
        raise TrustSafetyError("Only feature disablement can restore eligibility")
    if action.reversal_action_id:
        restored = await db.get(ModerationAction, action.reversal_action_id)
        assert restored
        return restored
    restored = ModerationAction(
        case_id=action.case_id,
        action_type=ModerationActionType.featured_placement_eligibility_restored,
        target_type=ReportTargetType.featured_placement,
        target_id=action.target_id,
        actor_user_id=actor.id,
        reason=reason,
    )
    db.add(restored)
    await db.flush()
    action.reversal_action_id, action.reversed_at = restored.id, datetime.now(UTC)
    return restored


async def reverse_content_containment(
    db: AsyncSession, action: ModerationAction, actor: User, reason: str
) -> ModerationAction:
    if action.reversal_action_id:
        return await db.get(ModerationAction, action.reversal_action_id)
    from app.content import service as content_service

    await content_service.restore_from_moderation(db, actor, action.target_id, reason)
    reversal = ModerationAction(
        case_id=action.case_id,
        action_type=ModerationActionType.content_restore,
        target_type=action.target_type,
        target_id=action.target_id,
        actor_user_id=actor.id,
        reason=reason,
    )
    db.add(reversal)
    await db.flush()
    action.reversal_action_id = reversal.id
    action.reversed_at = datetime.now(UTC)
    return reversal


async def submit_appeal(
    db: AsyncSession,
    action: ModerationAction,
    appellant: User,
    reason: str,
    *,
    now: datetime | None = None,
) -> ModerationAppeal:
    now = now or datetime.now(UTC)
    if action.target_type is not ReportTargetType.media:
        raise TrustSafetyError("This action is not appealable through this workflow")
    content = await db.get(ContentItem, action.target_id)
    if not content or content.created_by_user_id != appellant.id:
        raise TrustSafetyError("You are not authorized to appeal this action")
    deadline = action.created_at + APPEAL_WINDOW
    if now > deadline:
        raise TrustSafetyError("The appeal deadline has passed")
    existing = await db.scalar(
        select(ModerationAppeal).where(
            ModerationAppeal.moderation_action_id == action.id,
            ModerationAppeal.status.in_([AppealStatus.submitted, AppealStatus.under_review]),
        )
    )
    if existing:
        return existing
    appeal = ModerationAppeal(
        moderation_case_id=action.case_id,
        moderation_action_id=action.id,
        appellant_user_id=appellant.id,
        reason=reason.strip(),
        policy_deadline_at=deadline,
    )
    db.add(appeal)
    await db.flush()
    await emit_transactional(
        db,
        recipient_user_id=appellant.id,
        notification_type="APPEAL_SUBMITTED",
        source_domain="trust_safety",
        source_id=str(appeal.id),
        title="Appeal received",
        body="Your appeal has been received for review.",
        target_path="/appeals",
    )
    return appeal


async def decide_appeal(
    db: AsyncSession, appeal: ModerationAppeal, reviewer: User, outcome: AppealStatus, reason: str
) -> ModerationAppeal:
    if outcome not in {
        AppealStatus.upheld,
        AppealStatus.overturned,
        AppealStatus.partially_overturned,
    }:
        raise TrustSafetyError("Invalid appeal decision")
    action = await db.get(ModerationAction, appeal.moderation_action_id)
    case = await db.get(ModerationCase, appeal.moderation_case_id)
    assert action and case
    if (
        case.severity in {ModerationSeverity.high, ModerationSeverity.critical}
        and action.actor_user_id == reviewer.id
    ):
        raise TrustSafetyError("Original moderator cannot finalize a high-severity appeal")
    if (
        outcome is AppealStatus.overturned
        and action.action_type is ModerationActionType.temporary_containment
    ):
        await reverse_content_containment(db, action, reviewer, reason)
    appeal.status, appeal.reviewer_user_id, appeal.outcome, appeal.decided_at = (
        outcome,
        reviewer.id,
        reason.strip(),
        datetime.now(UTC),
    )
    await emit_transactional(
        db,
        recipient_user_id=appeal.appellant_user_id,
        notification_type="APPEAL_DECIDED",
        source_domain="trust_safety",
        source_id=str(appeal.id),
        title="Appeal decided",
        body="Your appeal decision is available in FanBackstage.",
        target_path="/appeals",
    )
    return appeal


async def submit_consent_release(
    db: AsyncSession,
    creator: CreatorProfile,
    actor: User,
    release_type: ConsentReleaseType,
    participant_reference: str,
    content_ids: list[UUID],
    effective_until: datetime | None = None,
    evidence_reference: str | None = None,
    supersedes_release_id: UUID | None = None,
) -> ConsentRelease:
    if (
        not await can_manage_consent_releases(db, creator, actor)
        or not participant_reference.strip()
        or not content_ids
    ):
        raise TrustSafetyError("Invalid consent release submission")
    for content_id in set(content_ids):
        content = await db.get(ContentItem, content_id)
        if not content or content.owner_creator_id != creator.id:
            raise TrustSafetyError("Consent scope contains unauthorized content")
    release = ConsentRelease(
        owner_creator_id=creator.id,
        release_type=release_type,
        status=ConsentReleaseStatus.pending,
        participant_reference=participant_reference.strip(),
        scope_snapshot={"content_ids": sorted(str(item) for item in set(content_ids))},
        evidence_reference=evidence_reference,
        effective_from=datetime.now(UTC),
        effective_until=effective_until,
        supersedes_release_id=supersedes_release_id,
        created_by_user_id=actor.id,
    )
    db.add(release)
    await db.flush()
    for content_id in set(content_ids):
        db.add(ConsentReleaseContent(consent_release_id=release.id, content_id=content_id))
    await emit_transactional(
        db,
        recipient_user_id=creator.user_id,
        notification_type="CONSENT_REVIEW_REQUESTED",
        source_domain="trust_safety",
        source_id=str(release.id),
        title="Consent review requested",
        body="A consent release is awaiting review.",
        target_path="/creator-studio/consent",
    )
    return release


async def verify_consent_release(
    db: AsyncSession, release: ConsentRelease, reviewer: User, approved: bool
) -> ConsentRelease:
    if release.created_by_user_id == reviewer.id:
        raise TrustSafetyError("Consent release cannot be self-verified")
    requested = ConsentReleaseStatus.verified if approved else ConsentReleaseStatus.rejected
    if release.status is requested:
        return release
    if release.status is not ConsentReleaseStatus.pending:
        raise TrustSafetyError("Consent release is not pending")
    release.status = ConsentReleaseStatus.verified if approved else ConsentReleaseStatus.rejected
    release.verified_at = datetime.now(UTC) if approved else None
    release.verified_by_user_id = reviewer.id if approved else None
    if approved and release.supersedes_release_id:
        prior = await db.get(ConsentRelease, release.supersedes_release_id)
        if prior and prior.status is ConsentReleaseStatus.verified:
            prior.status = ConsentReleaseStatus.superseded
    await record_event(
        db,
        "trust_safety.consent_release_reviewed",
        actor_user_id=reviewer.id,
        target_type="consent_release",
        target_id=str(release.id),
        metadata={"approved": approved},
    )
    creator = await db.get(CreatorProfile, release.owner_creator_id)
    assert creator is not None
    await emit_transactional(
        db,
        recipient_user_id=creator.user_id,
        notification_type="CONSENT_VERIFIED" if approved else "CONSENT_REJECTED",
        source_domain="trust_safety",
        source_id=str(release.id),
        title="Consent review completed",
        body="Your consent release review is available in FanBackstage.",
        target_path="/creator-studio/consent",
    )
    return release


async def valid_verified_release_for_content(
    db: AsyncSession, content_id: UUID, now: datetime | None = None
) -> bool:
    now = now or datetime.now(UTC)
    content = await db.get(ContentItem, content_id)
    if content is None:
        return False
    creator = await db.get(CreatorProfile, content.owner_creator_id)
    creator_policy = None
    if creator is not None:
        # Creator jurisdiction is owned by the creator compliance resolver. Do
        # not reconstruct it from profile/account fields in the performer
        # domain, because current KYC conflicts must retain their fail-closed
        # meaning.
        from app.creators.service import resolve_creator_compliance_eligibility

        creator_eligibility = await resolve_creator_compliance_eligibility(
            db,
            profile=creator,
            now=now,
        )
        if creator_eligibility.jurisdiction is not None:
            creator_policy = await effective_policy_for_country(
                db,
                creator_eligibility.jurisdiction,
                now=now,
            )
    strict_performer_authority = bool(
        creator_policy and creator_policy.rules.co_performer_verification_required
    )
    performers = (
        await db.scalars(
            select(VerifiedContentPerformer).where(
                VerifiedContentPerformer.content_id == content_id
            )
        )
    ).all()
    if strict_performer_authority and not performers:
        # A generic release cannot identify the performers it purports to
        # cover. Jurisdictions that require verified co-performers therefore
        # require explicit one-per-performer links before any release can be
        # treated as authority.
        return False
    if performers:
        # A release represents one named performer. Reusing it for multiple
        # linked people must never satisfy the all-performers invariant.
        required_release_ids: set[UUID] = set()
        for link in performers:
            performer = await db.get(PerformerIdentity, link.performer_id)
            if performer is None:
                return False
            linked_user = (
                await db.get(User, performer.platform_user_id)
                if performer.platform_user_id is not None
                else None
            )
            try:
                countries = {
                    country
                    for country in (
                        normalize_country_code(performer.country_code),
                        normalize_country_code(linked_user.country_code if linked_user else None),
                    )
                    if country is not None
                }
            except ValueError:
                return False
            if len(countries) != 1:
                return False
            country = countries.pop()
            policy = await effective_policy_for_country(db, country, now=now)
            if policy is None:
                return False
            identity_required = (
                strict_performer_authority
                or link.identity_verification_required
                or policy.rules.co_performer_verification_required
            )
            age_required = (
                strict_performer_authority
                or link.age_verification_required
                or policy.rules.co_performer_verification_required
            )
            release_required = (
                strict_performer_authority or link.release_required or policy.rules.release_required
            )
            if release_required:
                if (
                    link.consent_release_id is None
                    or link.consent_release_id in required_release_ids
                ):
                    return False
                required_release_ids.add(link.consent_release_id)
            if identity_required:
                identity = await db.scalar(
                    select(PerformerIdentityVerification)
                    .where(PerformerIdentityVerification.performer_id == performer.id)
                    .order_by(
                        PerformerIdentityVerification.created_at.desc(),
                        PerformerIdentityVerification.id.desc(),
                    )
                    .limit(1)
                )
                if (
                    identity is None
                    or identity.status is not PerformerIdentityStatus.verified
                    or identity.verified_at is None
                    or identity.revoked_at is not None
                    or (identity.expires_at is not None and identity.expires_at <= now)
                    or (
                        identity.country_code is not None
                        and normalize_country_code(identity.country_code) != country
                    )
                ):
                    return False
            if age_required:
                age = await db.scalar(
                    select(PerformerAgeVerification)
                    .where(PerformerAgeVerification.performer_id == performer.id)
                    .order_by(
                        PerformerAgeVerification.created_at.desc(),
                        PerformerAgeVerification.id.desc(),
                    )
                    .limit(1)
                )
                if (
                    age is None
                    or age.status is not AgeVerificationStatus.verified
                    or age.verified_at is None
                    or age.revoked_at is not None
                    or (age.expires_at is not None and age.expires_at <= now)
                    or normalize_country_code(age.country_code) != country
                    or age.required_minimum_age < policy.rules.minimum_age
                    or ASSURANCE_STRENGTH[age.achieved_assurance_level]
                    < ASSURANCE_STRENGTH[policy.rules.required_assurance_level]
                    or (
                        policy.rules.reverify_after_days is not None
                        and (
                            age.expires_at is None
                            or age.verified_at + timedelta(days=policy.rules.reverify_after_days)
                            <= now
                        )
                    )
                ):
                    return False
            if release_required:
                release = (
                    await db.get(ConsentRelease, link.consent_release_id)
                    if link.consent_release_id
                    else None
                )
                if (
                    release is None
                    or release.owner_creator_id != performer.owner_creator_id
                    or release.participant_reference != performer.safe_reference
                    or release.status is not ConsentReleaseStatus.verified
                    or release.verified_at is None
                    or release.revoked_at is not None
                    or release.effective_from is None
                    or release.effective_from > now
                    or (release.effective_until is not None and release.effective_until < now)
                    or not await db.scalar(
                        select(ConsentReleaseContent.consent_release_id).where(
                            ConsentReleaseContent.consent_release_id == release.id,
                            ConsentReleaseContent.content_id == content_id,
                        )
                    )
                ):
                    return False
        return True
    return bool(
        await db.scalar(
            select(ConsentRelease.id)
            .join(ConsentReleaseContent)
            .where(
                ConsentReleaseContent.content_id == content_id,
                ConsentRelease.status == ConsentReleaseStatus.verified,
                ConsentRelease.verified_at.is_not(None),
                ConsentRelease.revoked_at.is_(None),
                ConsentRelease.effective_from <= now,
                (
                    ConsentRelease.effective_until.is_(None)
                    | (ConsentRelease.effective_until >= now)
                ),
            )
        )
    )


async def creator_performer_consent_issue_count(
    db: AsyncSession,
    creator_profile_id: UUID,
    now: datetime | None = None,
) -> int:
    """Count creator content whose current performer/consent authority fails.

    A content item contributes at most one issue even when several linked
    performers fail. Deleted content is historical and is not an actionable
    Creator Studio item.
    """

    current = now or datetime.now(UTC)
    content_ids = (
        await db.scalars(
            select(ContentItem.id).where(
                ContentItem.owner_creator_id == creator_profile_id,
                ContentItem.deleted_at.is_(None),
                or_(
                    ContentItem.requires_verified_consent.is_(True),
                    exists().where(VerifiedContentPerformer.content_id == ContentItem.id),
                ),
            )
        )
    ).all()
    issues = 0
    for content_id in content_ids:
        if not await valid_verified_release_for_content(db, content_id, now=current):
            issues += 1
    return issues


async def has_verified_content_performers(db: AsyncSession, content_id: UUID) -> bool:
    """Whether the private all-performers authority applies to this content."""

    return bool(
        await db.scalar(
            select(VerifiedContentPerformer.id)
            .where(VerifiedContentPerformer.content_id == content_id)
            .limit(1)
        )
    )


async def _open_consent_review_case(
    db: AsyncSession,
    release: ConsentRelease,
    content_id: UUID,
    reason: str,
    effective_at: datetime,
    actor: User | None,
) -> None:
    case = await db.scalar(
        select(ModerationCase)
        .where(
            ModerationCase.primary_target_type == ReportTargetType.media,
            ModerationCase.primary_target_id == content_id,
            ModerationCase.queue == ModerationQueue.consent,
            ModerationCase.status.notin_(
                [ModerationCaseStatus.resolved, ModerationCaseStatus.dismissed]
            ),
        )
        .order_by(ModerationCase.created_at.desc())
    )
    if not case:
        case = ModerationCase(
            public_id=_case_public_id(),
            primary_target_type=ReportTargetType.media,
            primary_target_id=content_id,
            status=ModerationCaseStatus.action_required,
            severity=ModerationSeverity.high,
            priority=50,
            queue=ModerationQueue.consent,
            opened_at=effective_at,
            decision_summary=f"Consent release {reason}; review linked content eligibility.",
        )
        db.add(case)
        await db.flush()
    source_type = f"consent_release_{reason}"
    evidence = await db.scalar(
        select(ModerationEvidence).where(
            ModerationEvidence.case_id == case.id,
            ModerationEvidence.source_type == source_type,
            ModerationEvidence.source_id == release.id,
        )
    )
    if not evidence:
        db.add(
            ModerationEvidence(
                case_id=case.id,
                source_type=source_type,
                source_id=release.id,
                snapshot={
                    "release_id": str(release.id),
                    "content_id": str(content_id),
                    "creator_id": str(release.owner_creator_id),
                    "reason": "revoked" if reason == "revocation" else reason,
                    "effective_at": effective_at.isoformat(),
                },
                sensitive=False,
                created_by_user_id=actor.id if actor else None,
            )
        )


async def expire_consent_releases(db: AsyncSession, now: datetime | None = None) -> int:
    """Durably mark due releases expired and open one review case per linked content."""
    now = now or datetime.now(UTC)
    releases = list(
        await db.scalars(
            select(ConsentRelease)
            .where(
                ConsentRelease.status == ConsentReleaseStatus.verified,
                ConsentRelease.effective_until.is_not(None),
                ConsentRelease.effective_until < now,
            )
            .with_for_update(skip_locked=True)
        )
    )
    for release in releases:
        release.status = ConsentReleaseStatus.expired
        linked_content_ids = list(
            await db.scalars(
                select(ConsentReleaseContent.content_id).where(
                    ConsentReleaseContent.consent_release_id == release.id
                )
            )
        )
        for content_id in linked_content_ids:
            await _open_consent_review_case(db, release, content_id, "expired", now, None)
    return len(releases)


async def revoke_consent_release(
    db: AsyncSession, release: ConsentRelease, actor: User
) -> ConsentRelease:
    creator = await db.get(CreatorProfile, release.owner_creator_id)
    if not creator or not await can_manage_consent_releases(db, creator, actor):
        raise TrustSafetyError("Consent release cannot be revoked")
    if release.status is ConsentReleaseStatus.revoked:
        return release
    if release.status is not ConsentReleaseStatus.verified:
        raise TrustSafetyError("Consent release cannot be revoked")

    effective_at = datetime.now(UTC)
    release.status, release.revoked_at = ConsentReleaseStatus.revoked, effective_at
    linked_content_ids = list(
        await db.scalars(
            select(ConsentReleaseContent.content_id).where(
                ConsentReleaseContent.consent_release_id == release.id
            )
        )
    )
    for content_id in linked_content_ids:
        await _open_consent_review_case(db, release, content_id, "revocation", effective_at, actor)
    return release
