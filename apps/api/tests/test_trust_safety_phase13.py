from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.accounts import service as accounts
from app.api.routes import trust_safety as trust_safety_routes
from app.content import service as content_service
from app.creators import service as creators
from app.discovery import service as discovery
from app.groups import service as groups
from app.models.audit import AuditEvent
from app.models.content import ContentItem, ContentStatus, ContentType, ModerationStatus
from app.models.creator import CreatorStatus
from app.models.groups import GroupPermission
from app.models.social import FeedPost, FeedPostStatus, FeedPostType
from app.models.trust_safety import (
    ModerationCase,
    ModerationEvidence,
    ModerationQueue,
    ModerationSeverity,
    ReportReason,
    ReportTargetType,
    TrustSafetyReport,
)
from app.trust_safety import service


@pytest.mark.asyncio
async def test_central_report_preserves_context_aggregates_case_and_retains_duplicate(db_session):
    owner, _ = await accounts.register(
        db_session, "ts-owner@example.com", "strong-password-123", None
    )
    reporter, _ = await accounts.register(
        db_session, "ts-reporter@example.com", "strong-password-123", None
    )
    post = FeedPost(
        creator_id=(await creators.get_or_create_profile(db_session, owner)).id,
        created_by_user_id=owner.id,
        post_type=FeedPostType.text,
        body="original context that may later change",
        status=FeedPostStatus.published,
    )
    db_session.add(post)
    await db_session.flush()

    first, case, duplicate = await service.open_or_attach_report(
        db_session,
        reporter,
        target_type=ReportTargetType.post,
        target_id=post.id,
        reason=ReportReason.non_consensual_content,
        details="Please review",
    )
    post.body = "changed after report"
    second, same_case, duplicate = await service.open_or_attach_report(
        db_session,
        reporter,
        target_type=ReportTargetType.post,
        target_id=post.id,
        reason=ReportReason.non_consensual_content,
        details="Repeated signal",
    )

    assert case.id == same_case.id
    assert case.severity is ModerationSeverity.high
    assert case.queue is ModerationQueue.urgent
    assert duplicate and second.duplicate_of_report_id == first.id
    reports = (await db_session.scalars(select(TrustSafetyReport))).all()
    assert len(reports) == 2
    evidence = (await db_session.scalars(select(ModerationEvidence))).all()
    assert len(evidence) == 2
    assert evidence[0].snapshot["target_type"] == "post"
    assert evidence[0].snapshot["target_id"] == str(post.id)
    assert evidence[0].snapshot["status"] == "published"


@pytest.mark.asyncio
async def test_underage_report_is_critical_and_unknown_targets_fail_closed(db_session):
    reporter, _ = await accounts.register(
        db_session, "ts-critical@example.com", "strong-password-123", None
    )
    profile = await creators.get_or_create_profile(db_session, reporter)
    report, case, _ = await service.open_or_attach_report(
        db_session,
        reporter,
        target_type=ReportTargetType.creator,
        target_id=profile.id,
        reason=ReportReason.underage_concern,
        details=None,
    )
    assert report.case_id == case.id
    assert case.severity is ModerationSeverity.critical
    assert case.priority == 100


@pytest.mark.asyncio
async def test_containment_is_replay_safe_and_restoration_preserves_action_history(db_session):
    owner, _ = await accounts.register(
        db_session, "ts-content-owner@example.com", "strong-password-123", None
    )
    moderator, _ = await accounts.register(
        db_session, "ts-moderator@example.com", "strong-password-123", None
    )
    reporter, _ = await accounts.register(
        db_session, "ts-content-reporter@example.com", "strong-password-123", None
    )
    profile = await creators.get_or_create_profile(db_session, owner)
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Reportable",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
    )
    db_session.add(content)
    await db_session.flush()
    _, case, _ = await service.open_or_attach_report(
        db_session,
        reporter,
        target_type=ReportTargetType.media,
        target_id=content.id,
        reason=ReportReason.underage_concern,
        details="credible",
    )
    assert content.status is ContentStatus.removed
    action = await service.enforce_content_containment(
        db_session, case, moderator, content.id, "urgent containment"
    )
    assert (
        await service.enforce_content_containment(db_session, case, moderator, content.id, "again")
    ).id == action.id
    assert content.status is ContentStatus.removed
    reversal = await service.reverse_content_containment(
        db_session, action, moderator, "unsupported"
    )
    assert action.reversal_action_id == reversal.id and action.reversed_at
    assert content.status is ContentStatus.pending_review


@pytest.mark.asyncio
async def test_high_severity_appeal_deadline_and_reviewer_independence(db_session):
    owner, _ = await accounts.register(
        db_session, "ts-appeal-owner@example.com", "strong-password-123", None
    )
    original, _ = await accounts.register(
        db_session, "ts-original@example.com", "strong-password-123", None
    )
    reviewer, _ = await accounts.register(
        db_session, "ts-reviewer@example.com", "strong-password-123", None
    )
    reporter, _ = await accounts.register(
        db_session, "ts-appeal-reporter@example.com", "strong-password-123", None
    )
    profile = await creators.get_or_create_profile(db_session, owner)
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Appealable",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
    )
    db_session.add(content)
    await db_session.flush()
    _, case, _ = await service.open_or_attach_report(
        db_session,
        reporter,
        target_type=ReportTargetType.media,
        target_id=content.id,
        reason=ReportReason.non_consensual_content,
        details="credible",
    )
    action = await service.enforce_content_containment(
        db_session, case, original, content.id, "urgent"
    )
    appeal = await service.submit_appeal(db_session, action, owner, "unsupported")
    with pytest.raises(service.TrustSafetyError, match="Original moderator"):
        await service.decide_appeal(
            db_session, appeal, original, service.AppealStatus.overturned, "no"
        )
    decided = await service.decide_appeal(
        db_session, appeal, reviewer, service.AppealStatus.overturned, "supported"
    )
    assert decided.status is service.AppealStatus.overturned
    with pytest.raises(service.TrustSafetyError, match="deadline"):
        await service.submit_appeal(
            db_session,
            action,
            owner,
            "late",
            now=action.created_at + service.APPEAL_WINDOW + timedelta(seconds=1),
        )


@pytest.mark.asyncio
async def test_consent_submission_verification_scope_revocation_and_supersession(db_session):
    owner, _ = await accounts.register(
        db_session, "ts-consent-owner@example.com", "strong-password-123", None
    )
    reviewer, _ = await accounts.register(
        db_session, "ts-consent-reviewer@example.com", "strong-password-123", None
    )
    profile = await creators.get_or_create_profile(db_session, owner)
    first_content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Scoped",
        status=ContentStatus.published,
    )
    other_content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Other",
        status=ContentStatus.published,
    )
    db_session.add_all([first_content, other_content])
    await db_session.flush()
    release = await service.submit_consent_release(
        db_session,
        profile,
        owner,
        service.ConsentReleaseType.content_participation,
        "participant-private-reference",
        [first_content.id],
    )
    assert (
        release.status.value == "pending"
        and not await service.valid_verified_release_for_content(db_session, first_content.id)
    )
    with pytest.raises(service.TrustSafetyError):
        await service.verify_consent_release(db_session, release, owner, True)
    await service.verify_consent_release(db_session, release, reviewer, True)
    assert (
        await service.verify_consent_release(db_session, release, reviewer, True)
    ).id == release.id
    assert await service.valid_verified_release_for_content(db_session, first_content.id)
    assert not await service.valid_verified_release_for_content(db_session, other_content.id)
    replacement = await service.submit_consent_release(
        db_session,
        profile,
        owner,
        service.ConsentReleaseType.content_participation,
        "participant-private-reference",
        [first_content.id],
        supersedes_release_id=release.id,
    )
    await service.verify_consent_release(db_session, replacement, reviewer, True)
    assert release.status.value == "superseded" and replacement.status.value == "verified"
    await service.revoke_consent_release(db_session, replacement, owner)
    assert not await service.valid_verified_release_for_content(db_session, first_content.id)
    rejected = await service.submit_consent_release(
        db_session,
        profile,
        owner,
        service.ConsentReleaseType.content_participation,
        "participant-private-reference",
        [other_content.id],
    )
    await service.verify_consent_release(db_session, rejected, reviewer, False)
    assert (
        await service.verify_consent_release(db_session, rejected, reviewer, False)
    ).id == rejected.id
    with pytest.raises(service.TrustSafetyError, match="not pending"):
        await service.verify_consent_release(db_session, rejected, reviewer, True)


@pytest.mark.asyncio
async def test_mandatory_consent_fails_closed_until_current_verified_scope(db_session):
    owner, _ = await accounts.register(
        db_session, "ts-required-owner@example.com", "strong-password-123", None
    )
    reviewer, _ = await accounts.register(
        db_session, "ts-required-reviewer@example.com", "strong-password-123", None
    )
    profile = await creators.get_or_create_profile(db_session, owner)
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Requires release",
        status=ContentStatus.pending_review,
        requires_verified_consent=True,
    )
    db_session.add(content)
    await db_session.flush()
    with pytest.raises(ValueError, match="verified consent"):
        await content_service.approve(db_session, content, reviewer)
    release = await service.submit_consent_release(
        db_session,
        profile,
        owner,
        service.ConsentReleaseType.co_performer_release,
        "participant",
        [content.id],
    )
    await service.verify_consent_release(db_session, release, reviewer, True)
    await content_service.approve(db_session, content, reviewer)
    assert content.status is ContentStatus.published
    await service.revoke_consent_release(db_session, release, owner)
    from app.content.access import can_access_content

    assert not await can_access_content(db_session, content, None)


@pytest.mark.asyncio
async def test_revoked_or_expired_required_consent_disappears_from_discovery(db_session):
    owner, _ = await accounts.register(
        db_session, "ts-discovery-owner@example.com", "strong-password-123", None
    )
    reviewer, _ = await accounts.register(
        db_session, "ts-discovery-reviewer@example.com", "strong-password-123", None
    )
    profile = await creators.get_or_create_profile(db_session, owner)
    profile.status, profile.is_public = CreatorStatus.approved, True
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Consent discovery target",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        requires_verified_consent=True,
        published_at=datetime.now(UTC),
    )
    db_session.add(content)
    await db_session.flush()
    release = await service.submit_consent_release(
        db_session,
        profile,
        owner,
        service.ConsentReleaseType.co_performer_release,
        "participant",
        [content.id],
    )
    await service.verify_consent_release(db_session, release, reviewer, True)
    rows, _, _ = await discovery.search(db_session, None, query="consent discovery")
    assert content.id in {row.id for row in rows}
    await service.revoke_consent_release(db_session, release, owner)
    rows, _, _ = await discovery.search(db_session, None, query="consent discovery")
    assert content.id not in {row.id for row in rows}


@pytest.mark.asyncio
async def test_expired_and_superseded_releases_cannot_authorize_new_serving(db_session):
    owner, _ = await accounts.register(
        db_session, "ts-expiry-owner@example.com", "strong-password-123", None
    )
    reviewer, _ = await accounts.register(
        db_session, "ts-expiry-reviewer@example.com", "strong-password-123", None
    )
    profile = await creators.get_or_create_profile(db_session, owner)
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Expiry",
        requires_verified_consent=True,
    )
    db_session.add(content)
    await db_session.flush()
    expired = await service.submit_consent_release(
        db_session,
        profile,
        owner,
        service.ConsentReleaseType.co_performer_release,
        "participant",
        [content.id],
        effective_until=datetime.now(UTC) - timedelta(seconds=1),
    )
    await service.verify_consent_release(db_session, expired, reviewer, True)
    assert not await service.valid_verified_release_for_content(db_session, content.id)
    assert await service.expire_consent_releases(db_session) == 1
    assert expired.status.value == "expired"
    expiry_case = await db_session.scalar(
        select(ModerationCase).where(
            ModerationCase.primary_target_id == content.id,
            ModerationCase.queue == ModerationQueue.consent,
        )
    )
    assert expiry_case
    assert await service.expire_consent_releases(db_session) == 0
    current = await service.submit_consent_release(
        db_session,
        profile,
        owner,
        service.ConsentReleaseType.co_performer_release,
        "participant",
        [content.id],
        supersedes_release_id=expired.id,
    )
    await service.verify_consent_release(db_session, current, reviewer, True)
    assert expired.status.value == "expired"
    assert await service.valid_verified_release_for_content(db_session, content.id)


@pytest.mark.asyncio
async def test_revocation_opens_one_persistent_consent_review_case(db_session):
    owner, _ = await accounts.register(
        db_session, "ts-revocation-case-owner@example.com", "strong-password-123", None
    )
    reviewer, _ = await accounts.register(
        db_session, "ts-revocation-case-reviewer@example.com", "strong-password-123", None
    )
    profile = await creators.get_or_create_profile(db_session, owner)
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Revocation case",
        requires_verified_consent=True,
    )
    db_session.add(content)
    await db_session.flush()
    release = await service.submit_consent_release(
        db_session,
        profile,
        owner,
        service.ConsentReleaseType.co_performer_release,
        "participant",
        [content.id],
    )
    await service.verify_consent_release(db_session, release, reviewer, True)
    await service.revoke_consent_release(db_session, release, owner)
    await service.revoke_consent_release(db_session, release, owner)
    cases = list(
        await db_session.scalars(
            select(ModerationCase).where(
                ModerationCase.primary_target_id == content.id,
                ModerationCase.queue == ModerationQueue.consent,
            )
        )
    )
    assert len(cases) == 1
    evidence = list(
        await db_session.scalars(
            select(ModerationEvidence).where(ModerationEvidence.case_id == cases[0].id)
        )
    )
    assert len(evidence) == 1
    assert evidence[0].snapshot == {
        "release_id": str(release.id),
        "content_id": str(content.id),
        "creator_id": str(profile.id),
        "reason": "revoked",
        "effective_at": release.revoked_at.isoformat(),
    }
    replacement = await service.submit_consent_release(
        db_session,
        profile,
        owner,
        service.ConsentReleaseType.co_performer_release,
        "participant",
        [content.id],
        supersedes_release_id=release.id,
    )
    await service.verify_consent_release(db_session, replacement, reviewer, True)
    assert (
        len(
            list(
                await db_session.scalars(
                    select(ModerationCase).where(
                        ModerationCase.primary_target_id == content.id,
                        ModerationCase.queue == ModerationQueue.consent,
                    )
                )
            )
        )
        == 1
    )
    assert release.status.value == "revoked"
    assert await service.valid_verified_release_for_content(db_session, content.id)


@pytest.mark.asyncio
async def test_only_explicitly_delegated_manager_can_manage_but_not_verify_release(db_session):
    manager, _ = await accounts.register(
        db_session, "ts-consent-manager@example.com", "strong-password-123", None
    )
    creator_user, _ = await accounts.register(
        db_session, "ts-consent-managed@example.com", "strong-password-123", None
    )
    creator = await creators.get_or_create_profile(db_session, creator_user)
    content = ContentItem(
        owner_creator_id=creator.id,
        created_by_user_id=creator_user.id,
        content_type=ContentType.gallery,
        title="Managed consent",
    )
    db_session.add(content)
    await db_session.flush()
    group = await groups.create_group(
        db_session, manager, "Consent Group", "consent-group", 5000, None
    )
    membership = await groups.invite_creator(
        db_session,
        group.id,
        manager,
        creator.id,
        None,
        [GroupPermission.manage_consent_releases],
    )
    await groups.accept_invitation(db_session, membership.id, creator_user)
    assert await service.can_manage_consent_releases(db_session, creator, manager)
    release = await service.submit_consent_release(
        db_session,
        creator,
        manager,
        service.ConsentReleaseType.co_performer_release,
        "participant",
        [content.id],
    )
    with pytest.raises(service.TrustSafetyError, match="self-verified"):
        await service.verify_consent_release(db_session, release, manager, True)


@pytest.mark.asyncio
async def test_sensitive_evidence_is_privileged_audited_and_never_returned_raw(db_session):
    owner, _ = await accounts.register(
        db_session, "ts-sensitive-owner@example.com", "strong-password-123", None
    )
    manager, _ = await accounts.register(
        db_session, "ts-sensitive-manager@example.com", "strong-password-123", None
    )
    administrator, _ = await accounts.register(
        db_session, "ts-sensitive-admin@example.com", "strong-password-123", None
    )
    await accounts.assign_role(db_session, manager, "manager", manager.id, None)
    await accounts.assign_role(db_session, manager, "moderator", manager.id, None)
    await accounts.assign_role(db_session, administrator, "super_admin", administrator.id, None)
    profile = await creators.get_or_create_profile(db_session, owner)
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Sensitive evidence",
    )
    db_session.add(content)
    await db_session.flush()
    case = ModerationCase(
        public_id="TS-SENSITIVE",
        primary_target_type=ReportTargetType.media,
        primary_target_id=content.id,
        severity=ModerationSeverity.high,
        queue=ModerationQueue.consent,
        opened_at=datetime.now(UTC),
    )
    db_session.add(case)
    await db_session.flush()
    evidence = ModerationEvidence(
        case_id=case.id,
        source_type="consent_release_document",
        snapshot={"identity_document": "never-return-this", "safe": "case-context"},
        safe_reference="protected://release/opaque-id",
        sensitive=True,
    )
    db_session.add(evidence)
    await db_session.flush()
    detail = await trust_safety_routes.case_detail(case.id, (manager, None), db_session)
    assert "reporter_user_id" not in detail
    assert detail["safe_evidence"] == []
    with pytest.raises(HTTPException, match="Permission denied"):
        await trust_safety_routes.evidence_access(case.id, evidence.id, (manager, None), db_session)
    response = await trust_safety_routes.evidence_access(
        case.id, evidence.id, (administrator, None), db_session
    )
    assert response["snapshot"] is None and response["sensitive"] is True
    event = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "trust_safety.evidence_accessed")
    )
    assert event and event.metadata_json == {}


@pytest.mark.asyncio
async def test_creator_suspension_uses_creator_lifecycle_and_preserves_action_history(db_session):
    owner, _ = await accounts.register(
        db_session, "ts-suspend-owner@example.com", "strong-password-123", None
    )
    moderator, _ = await accounts.register(
        db_session, "ts-suspend-moderator@example.com", "strong-password-123", None
    )
    creator = await creators.get_or_create_profile(db_session, owner)
    creator.status, creator.is_public = CreatorStatus.approved, True
    case = ModerationCase(
        public_id="TS-CREATOR-SUSPEND",
        primary_target_type=ReportTargetType.creator,
        primary_target_id=creator.id,
        severity=ModerationSeverity.high,
        queue=ModerationQueue.general,
        opened_at=datetime.now(UTC),
    )
    db_session.add(case)
    await db_session.flush()
    action = await service.enforce_creator_suspension(
        db_session, case, moderator, creator.id, "supported safety finding"
    )
    assert creator.status is CreatorStatus.suspended and creator.is_public is False
    assert (
        await service.enforce_creator_suspension(db_session, case, moderator, creator.id, "replay")
    ).id == action.id
    restoration = await service.reverse_creator_suspension(
        db_session, action, moderator, "supported review"
    )
    assert creator.status is CreatorStatus.approved and creator.is_public is True
    assert action.reversal_action_id == restoration.id and action.reversed_at


@pytest.mark.asyncio
async def test_feature_eligibility_restoration_never_resurrects_terminal_booking(db_session):
    moderator, _ = await accounts.register(
        db_session, "ts-feature-restore@example.com", "strong-password-123", None
    )
    booking_id = uuid4()
    case = ModerationCase(
        public_id="TS-FEATURE-RESTORE",
        primary_target_type=ReportTargetType.featured_placement,
        primary_target_id=booking_id,
        severity=ModerationSeverity.high,
        queue=ModerationQueue.content,
        opened_at=datetime.now(UTC),
    )
    db_session.add(case)
    await db_session.flush()
    disabled = service.ModerationAction(
        case_id=case.id,
        action_type=service.ModerationActionType.featured_placement_disable,
        target_type=ReportTargetType.featured_placement,
        target_id=booking_id,
        actor_user_id=moderator.id,
        reason="moderation",
    )
    db_session.add(disabled)
    await db_session.flush()
    restored = await service.restore_feature_eligibility(db_session, disabled, moderator, "appeal")
    assert restored.target_id == booking_id
    assert disabled.reversal_action_id == restored.id
    assert (
        await service.restore_feature_eligibility(db_session, disabled, moderator, "replay")
    ).id == restored.id


@pytest.mark.asyncio
async def test_case_assignment_and_notes_require_server_side_triage_permission(db_session):
    user, _ = await accounts.register(
        db_session, "ts-case-user@example.com", "strong-password-123", None
    )
    target = uuid4()
    case = ModerationCase(
        public_id="TS-CONTROLS",
        primary_target_type=ReportTargetType.media,
        primary_target_id=target,
        severity=ModerationSeverity.medium,
        queue=ModerationQueue.content,
        opened_at=datetime.now(UTC),
    )
    db_session.add(case)
    await db_session.flush()
    with pytest.raises(HTTPException, match="Permission denied"):
        await trust_safety_routes.assign_case(
            case.id, trust_safety_routes.CaseAssignmentInput(), (user, None), db_session
        )
    with pytest.raises(HTTPException, match="Permission denied"):
        await trust_safety_routes.add_note(
            case.id, trust_safety_routes.CaseNoteInput(body="nope"), (user, None), db_session
        )


@pytest.mark.asyncio
async def test_enforcement_endpoint_denies_crafted_unauthorized_and_unsupported_actions(db_session):
    user, _ = await accounts.register(
        db_session, "ts-enforce-user@example.com", "strong-password-123", None
    )
    moderator, _ = await accounts.register(
        db_session, "ts-enforce-mod@example.com", "strong-password-123", None
    )
    await accounts.assign_role(db_session, moderator, "moderator", moderator.id, None)
    case = ModerationCase(
        public_id="TS-ENFORCE",
        primary_target_type=ReportTargetType.media,
        primary_target_id=uuid4(),
        severity=ModerationSeverity.medium,
        queue=ModerationQueue.content,
        opened_at=datetime.now(UTC),
    )
    db_session.add(case)
    await db_session.flush()
    payload = trust_safety_routes.EnforcementInput(
        action="unsupported", target_id=uuid4(), reason="test"
    )
    with pytest.raises(HTTPException, match="Permission denied"):
        await trust_safety_routes.enforce_case(case.id, payload, (user, None), db_session)
    with pytest.raises(HTTPException, match="Unsupported"):
        await trust_safety_routes.enforce_case(case.id, payload, (moderator, None), db_session)
