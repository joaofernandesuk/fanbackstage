from datetime import timedelta

import pytest
from sqlalchemy import select

from app.accounts import service as accounts
from app.creators import service as creators
from app.models.social import FeedPost, FeedPostStatus, FeedPostType
from app.models.content import ContentItem, ContentStatus, ContentType, ModerationStatus
from app.models.trust_safety import (
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
        reason=ReportReason.underage_concern,
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
