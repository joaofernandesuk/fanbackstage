from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app.accounts import service as accounts
from app.api.routes import finance as finance_routes
from app.api.routes import trust_safety as trust_safety_routes
from app.creators import service as creators
from app.finance import operations as finance_operations
from app.models.audit import AuditEvent
from app.models.compliance import PerformerIdentity
from app.models.content import ContentItem, ContentStatus, ContentType, ModerationStatus
from app.models.creator import CreatorStatus, CreatorVerification, VerificationStatus
from app.models.finance import PaymentAttempt, PaymentStatus, StagingPaymentSandboxEvent
from app.models.trust_safety import AppealStatus, ReportReason, ReportTargetType
from app.permissions.policies import Permission, authorize
from app.schemas.finance import FinanceRefundOperationInput
from app.trust_safety import service as trust_safety


@pytest.mark.asyncio
async def test_finance_operations_are_bounded_safe_and_refund_commands_are_idempotent(db_session):
    buyer, _ = await accounts.register(
        db_session, "operations-buyer@example.com", "strong-password-123", None
    )
    admin, _ = await accounts.register(
        db_session, "operations-admin@example.com", "strong-password-123", None
    )
    super_admin, _ = await accounts.register(
        db_session, "operations-super@example.com", "strong-password-123", None
    )
    await accounts.assign_role(db_session, admin, "admin", super_admin.id, "test")
    await accounts.assign_role(db_session, super_admin, "super_admin", super_admin.id, "test")
    await db_session.refresh(admin, ["roles"])
    await db_session.refresh(super_admin, ["roles"])
    attempt = PaymentAttempt(
        buyer_user_id=buyer.id,
        provider="staging_sandbox",
        provider_reference="stg_operations_ref",
        amount_minor=1299,
        currency="EUR",
        status=PaymentStatus.disputed,
        idempotency_key="operations-refund",
        completed_at=datetime.now(UTC),
    )
    db_session.add(attempt)
    await db_session.flush()

    result = await finance_operations.search_payments(
        db_session, search="stg_operations", page=1, page_size=1
    )
    assert result["total"] == 1 and result["items"][0]["amount_minor"] == 1299
    exceptions = await finance_operations.search_payments(
        db_session, exceptions_only=True, page=1, page_size=25
    )
    assert exceptions["total"] == 1
    assert (await finance_operations.exception_counts(db_session))["open_disputes"] == 1
    detail = await finance_operations.payment_detail(db_session, attempt.id)
    assert detail and detail["provider_events"] == [] and "payload" not in str(detail)
    authorize(admin, Permission.FINANCIAL_ACCESS)
    with pytest.raises(HTTPException):
        authorize(admin, Permission.FINANCIAL_REFUND)

    payload = FinanceRefundOperationInput(reason="Customer requested a full refund", confirmed=True)
    first = await finance_routes.request_finance_refund(
        attempt.id, payload, (super_admin, None), db_session
    )
    second = await finance_routes.request_finance_refund(
        attempt.id, payload, (super_admin, None), db_session
    )
    assert first["queued"] is True and second["queued"] is False
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(StagingPaymentSandboxEvent)
            .where(StagingPaymentSandboxEvent.payment_attempt_id == attempt.id)
        )
    ) == 1


@pytest.mark.asyncio
async def test_creator_kyc_manual_review_preserves_provider_authority_and_rejects_stale_decision(
    db_session,
):
    creator_user, _ = await accounts.register(
        db_session, "operations-kyc@example.com", "strong-password-123", None
    )
    reviewer, _ = await accounts.register(
        db_session, "operations-kyc-reviewer@example.com", "strong-password-123", None
    )
    profile = await creators.get_or_create_profile(db_session, creator_user)
    profile.username = "operations-kyc"
    profile.status = CreatorStatus.pending_verification
    verification = CreatorVerification(
        creator_profile_id=profile.id,
        provider="staging_sandbox",
        provider_reference="stg_kyc_operations",
        status=VerificationStatus.needs_review,
        adult_verified=False,
        identity_verified=False,
        country_code="PT",
        metadata_json={"review_category": "provider_manual_review"},
    )
    db_session.add(verification)
    await db_session.flush()

    with pytest.raises(ValueError, match="approval"):
        await creators.review_creator_kyc(
            db_session,
            verification_id=verification.id,
            reviewer=reviewer,
            action="approve",
            reason="Unsupported manual approval",
        )
    decided = await creators.review_creator_kyc(
        db_session,
        verification_id=verification.id,
        reviewer=reviewer,
        action="request_reverification",
        reason="Provider requested a new capture",
    )
    assert decided.status is VerificationStatus.failed
    assert decided.failure_reason_code == "reverification_requested"
    with pytest.raises(ValueError, match="changed"):
        await creators.review_creator_kyc(
            db_session,
            verification_id=verification.id,
            reviewer=reviewer,
            action="reject",
            reason="Second operator stale decision",
        )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.event_type == "creator.verification_manual_reviewed")
        )
        == 1
    )


async def _appealable_action(db_session):
    owner, _ = await accounts.register(
        db_session, "operations-appeal-owner@example.com", "strong-password-123", None
    )
    reporter, _ = await accounts.register(
        db_session, "operations-appeal-reporter@example.com", "strong-password-123", None
    )
    moderator, _ = await accounts.register(
        db_session, "operations-appeal-moderator@example.com", "strong-password-123", None
    )
    reviewer, _ = await accounts.register(
        db_session, "operations-appeal-reviewer@example.com", "strong-password-123", None
    )
    await accounts.assign_role(db_session, reviewer, "moderator", reviewer.id, "test")
    await db_session.flush()
    await db_session.refresh(reviewer, ["roles"])
    profile = await creators.get_or_create_profile(db_session, owner)
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Operations appeal target",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
    )
    db_session.add(content)
    await db_session.flush()
    _, case, _ = await trust_safety.open_or_attach_report(
        db_session,
        reporter,
        target_type=ReportTargetType.media,
        target_id=content.id,
        reason=ReportReason.non_consensual_content,
        details="Review signal",
    )
    action = await trust_safety.enforce_content_containment(
        db_session, case, moderator, content.id, "Contain while reviewed"
    )
    return owner, reviewer, action


@pytest.mark.asyncio
async def test_appeal_selection_queue_audit_and_stale_decision_guard(db_session):
    owner, reviewer, action = await _appealable_action(db_session)
    eligible = await trust_safety_routes.eligible_appeals((owner, None), db_session)
    assert eligible["items"][0]["action_id"] == str(action.id)
    appeal = await trust_safety.submit_appeal(db_session, action, owner, "The action is mistaken")
    queue = await trust_safety_routes.appeal_operations(
        (reviewer, None),
        db_session,
        status=AppealStatus.submitted,
        action_type=None,
        search=None,
        assigned_to_me=False,
        starts_at=None,
        ends_at=None,
        page=1,
        page_size=25,
    )
    assert queue["items"][0]["id"] == str(appeal.id)
    await trust_safety.claim_appeal(db_session, appeal.id, reviewer)
    await trust_safety.decide_appeal(
        db_session, appeal, reviewer, AppealStatus.overturned, "Evidence supports reversal"
    )
    with pytest.raises(trust_safety.TrustSafetyError, match="changed"):
        await trust_safety.decide_appeal(
            db_session, appeal, reviewer, AppealStatus.upheld, "Stale competing decision"
        )
    assert appeal.status is AppealStatus.overturned


@pytest.mark.asyncio
async def test_consent_context_is_creator_scoped_and_sensitive_evidence_is_separated(db_session):
    owner, _ = await accounts.register(
        db_session, "operations-consent-owner@example.com", "strong-password-123", None
    )
    reviewer, _ = await accounts.register(
        db_session, "operations-consent-reviewer@example.com", "strong-password-123", None
    )
    super_admin, _ = await accounts.register(
        db_session, "operations-consent-super@example.com", "strong-password-123", None
    )
    await accounts.assign_role(db_session, reviewer, "moderator", super_admin.id, "test")
    await accounts.assign_role(db_session, super_admin, "super_admin", super_admin.id, "test")
    await db_session.refresh(reviewer, ["roles"])
    await db_session.refresh(super_admin, ["roles"])
    profile = await creators.get_or_create_profile(db_session, owner)
    profile.username = "operations-consent"
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Creator-owned consent content",
        status=ContentStatus.draft,
    )
    performer = PerformerIdentity(
        owner_creator_id=profile.id,
        safe_reference="Performer Alpha",
        country_code="PT",
        created_by_user_id=owner.id,
    )
    db_session.add_all([content, performer])
    await db_session.flush()
    release = await trust_safety.submit_consent_release(
        db_session,
        profile,
        owner,
        trust_safety.ConsentReleaseType.co_performer_release,
        performer.safe_reference,
        [content.id],
        evidence_reference="private/evidence/reference",
    )
    context = await trust_safety_routes.creator_consent_context((owner, None), db_session)
    assert context["contents"][0]["id"] == str(content.id)
    assert context["performers"][0]["label"] == performer.safe_reference
    ordinary = await trust_safety_routes.consent_operation_detail(
        release.id, (reviewer, None), db_session
    )
    sensitive = await trust_safety_routes.consent_operation_detail(
        release.id, (super_admin, None), db_session
    )
    assert ordinary["has_evidence"] is True and ordinary["evidence_reference"] is None
    assert "email" not in ordinary["creator"]
    assert ordinary["performer_verification"] == {
        "registered": True,
        "identity_status": None,
        "age_status": None,
    }
    assert sensitive["evidence_reference"] == "private/evidence/reference"
    await trust_safety.verify_consent_release(
        db_session, release, reviewer, True, "Evidence and scope were verified"
    )
    with pytest.raises(trust_safety.TrustSafetyError, match="changed"):
        await trust_safety.verify_consent_release(
            db_session, release, reviewer, False, "Stale competing review outcome"
        )
    consent_audits = set(
        await db_session.scalars(
            select(AuditEvent.event_type).where(AuditEvent.target_id == str(release.id))
        )
    )
    assert {
        "trust_safety.consent_release_submitted",
        "trust_safety.consent_release_reviewed",
    } <= consent_audits
