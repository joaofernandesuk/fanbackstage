from datetime import UTC, datetime, timedelta

import pytest
from conftest import trusted_self_attested_accounts as accounts
from sqlalchemy import select

from app.compliance.policy import create_jurisdiction_revision, create_template_revision
from app.compliance.types import PolicyOverrides, PolicyRules
from app.content import service as content_service
from app.content.access import (
    can_access_asset,
    can_access_content,
    public_content_surface_eligible,
)
from app.creators import service as creators
from app.models.audit import AuditEvent
from app.models.compliance import (
    AgeAssuranceLevel,
    AgeVerificationStatus,
    CompliancePolicyStatus,
    CompliancePolicyTemplateRevision,
    PerformerAgeVerification,
    PerformerIdentity,
    PerformerIdentityStatus,
    PerformerIdentityVerification,
    VerifiedContentPerformer,
)
from app.models.content import (
    AccessPolicy,
    ContentItem,
    ContentStatus,
    ContentType,
    Gallery,
    GalleryItem,
    MediaAsset,
    MediaAudience,
    MediaStatus,
    MediaType,
    ModerationStatus,
)
from app.models.creator import CreatorStatus
from app.performers import service as performers
from app.trust_safety import service as trust_safety


async def approved_creator(db, email: str):
    user, _ = await accounts.register(
        db,
        email,
        "strong-password-123",
        None,
        country_code="PT",
    )
    profile = await creators.get_or_create_profile(db, user)
    await creators.update_profile(
        db,
        profile,
        {
            "username": email.split("@")[0],
            "display_name": "Performer test creator",
            "country_code": "PT",
        },
        user.id,
    )
    await creators.submit(db, profile, user.id)
    await creators.development_verify(db, profile, True, user.id)
    await creators.set_status(db, profile, CreatorStatus.approved, user.id)
    profile.is_public = True
    return user, profile


async def strict_performer_policy(db, **rule_updates) -> None:
    revision = await db.scalar(
        select(CompliancePolicyTemplateRevision)
        .order_by(CompliancePolicyTemplateRevision.version.desc())
        .limit(1)
    )
    assert revision is not None
    assert revision.reviewed_by_user_id is not None
    now = datetime.now(UTC)
    updates = {
        "co_performer_verification_required": True,
        "release_required": False,
    }
    updates.update(rule_updates)
    rules = PolicyRules.model_validate(revision.rules_json).model_copy(update=updates)
    successor = await create_template_revision(
        db,
        template_id=revision.template_id,
        rules=rules,
        status=CompliancePolicyStatus.active,
        effective_from=now - timedelta(seconds=1),
        effective_until=None,
        actor_user_id=revision.reviewed_by_user_id,
        reviewed_at=now,
        reviewed_by_user_id=revision.reviewed_by_user_id,
        change_reason="Require verified co-performers in performer tests",
        is_demo=True,
    )
    await create_jurisdiction_revision(
        db,
        country_code="PT",
        template_revision_id=successor.id,
        overrides=PolicyOverrides(),
        status=CompliancePolicyStatus.active,
        effective_from=now - timedelta(seconds=1),
        effective_until=None,
        actor_user_id=revision.reviewed_by_user_id,
        reviewed_at=now,
        reviewed_by_user_id=revision.reviewed_by_user_id,
        change_reason="Apply verified co-performer policy in performer tests",
        is_demo=True,
    )


@pytest.mark.asyncio
async def test_creator_cannot_attach_an_arbitrary_platform_user_or_weaken_policy(
    db_session, reviewed_pt_compliance_policy
):
    owner, profile = await approved_creator(db_session, "performer-owner@example.com")
    other, _ = await accounts.register(
        db_session,
        "performer-other@example.com",
        "strong-password-123",
        None,
        country_code="PT",
    )
    with pytest.raises(performers.PerformerError, match="only attach their own"):
        await performers.create_identity(
            db_session,
            owner,
            "co-performer",
            platform_user_id=other.id,
            country_code="PT",
        )

    performer = await performers.create_identity(
        db_session,
        owner,
        "co-performer",
        country_code="PT",
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Policy-scoped content",
        status=ContentStatus.draft,
        access_policy=AccessPolicy.free,
    )
    db_session.add(content)
    await db_session.flush()
    await strict_performer_policy(db_session)

    with pytest.raises(performers.PerformerError, match="cannot be weakened"):
        await performers.link_content_performer(
            db_session,
            owner,
            content.id,
            performer.id,
            None,
            identity_verification_required=False,
        )
    assert await db_session.scalar(select(VerifiedContentPerformer.id)) is None

    with pytest.raises(performers.PerformerError, match="performer-specific release"):
        await performers.link_content_performer(
            db_session,
            owner,
            content.id,
            performer.id,
            None,
        )
    release = await trust_safety.submit_consent_release(
        db_session,
        profile,
        owner,
        trust_safety.ConsentReleaseType.co_performer_release,
        performer.safe_reference,
        [content.id],
    )
    await trust_safety.verify_consent_release(db_session, release, other, True)
    link = await performers.link_content_performer(
        db_session,
        owner,
        content.id,
        performer.id,
        release.id,
    )
    assert link.identity_verification_required
    assert link.age_verification_required
    assert link.release_required


@pytest.mark.asyncio
async def test_release_only_fallback_remains_for_non_strict_creator_policy(
    db_session, reviewed_pt_compliance_policy
):
    owner, profile = await approved_creator(db_session, "performer-legacy-release@example.com")
    reviewer, _ = await accounts.register(
        db_session,
        "performer-legacy-release-reviewer@example.com",
        "strong-password-123",
        None,
        country_code="PT",
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Legacy release content",
        status=ContentStatus.pending_review,
        moderation_status=ModerationStatus.queued,
        access_policy=AccessPolicy.free,
        requires_verified_consent=True,
    )
    db_session.add(content)
    await db_session.flush()
    release = await trust_safety.submit_consent_release(
        db_session,
        profile,
        owner,
        trust_safety.ConsentReleaseType.co_performer_release,
        "legacy release participant",
        [content.id],
    )
    await trust_safety.verify_consent_release(db_session, release, reviewer, True)

    assert await trust_safety.valid_verified_release_for_content(db_session, content.id)
    await content_service.approve(db_session, content, reviewer)
    assert await public_content_surface_eligible(db_session, content)
    assert await can_access_content(db_session, content, None)


@pytest.mark.asyncio
async def test_strict_creator_policy_rejects_release_only_content_until_each_performer_is_current(
    db_session, reviewed_pt_compliance_policy
):
    owner, profile = await approved_creator(db_session, "performer-strict-serving@example.com")
    reviewer, _ = await accounts.register(
        db_session,
        "performer-strict-serving-reviewer@example.com",
        "strong-password-123",
        None,
        country_code="PT",
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Strict performer authority content",
        status=ContentStatus.pending_review,
        moderation_status=ModerationStatus.queued,
        access_policy=AccessPolicy.free,
        requires_verified_consent=True,
    )
    content.gallery = Gallery(preview_count=0)
    asset = MediaAsset(
        owner_creator_id=profile.id,
        media_type=MediaType.image,
        status=MediaStatus.ready,
        storage_key="original/strict-performer-authority",
        original_filename="strict-performer-authority.jpg",
        mime_type="image/jpeg",
        audience=MediaAudience.safe_public,
    )
    db_session.add_all([content, asset])
    await db_session.flush()
    db_session.add(
        GalleryItem(
            gallery_id=content.gallery.id,
            media_asset_id=asset.id,
            position=0,
        )
    )
    release = await trust_safety.submit_consent_release(
        db_session,
        profile,
        owner,
        trust_safety.ConsentReleaseType.co_performer_release,
        "strict linked performer",
        [content.id],
    )
    await trust_safety.verify_consent_release(db_session, release, reviewer, True)
    await strict_performer_policy(db_session)

    # A current release without an explicit performer link is not sufficient
    # under the creator's effective strict co-performer policy.
    assert not await trust_safety.valid_verified_release_for_content(db_session, content.id)
    with pytest.raises(ValueError, match="verified consent release"):
        await content_service.approve(db_session, content, reviewer)

    # Legacy published rows are contained by both public projection and the
    # final full-asset resolver, even if they predate the strict policy.
    content.status = ContentStatus.published
    content.moderation_status = ModerationStatus.approved
    assert not await public_content_surface_eligible(db_session, content)
    assert not await can_access_content(db_session, content, None)
    assert not await can_access_asset(db_session, asset.id, None)
    content.status = ContentStatus.pending_review
    content.moderation_status = ModerationStatus.queued

    performer = await performers.create_identity(
        db_session,
        owner,
        release.participant_reference,
        country_code="PT",
    )
    # A historical link may snapshot weaker booleans. Current creator policy,
    # not that snapshot, remains authoritative at every serving decision.
    link = VerifiedContentPerformer(
        content_id=content.id,
        performer_id=performer.id,
        consent_release_id=release.id,
        identity_verification_required=False,
        age_verification_required=False,
        release_required=False,
    )
    db_session.add(link)
    await db_session.flush()
    assert not await trust_safety.valid_verified_release_for_content(db_session, content.id)

    expires_at = datetime.now(UTC) + timedelta(days=1)
    await performers.record_identity_verification(
        db_session,
        reviewer,
        performer.id,
        provider="manual",
        provider_reference="strict-serving-identity",
        status=PerformerIdentityStatus.verified,
        country_code="PT",
        expires_at=expires_at,
        confirmed=True,
        reason="Reviewed strict performer identity evidence",
    )
    await performers.record_age_verification(
        db_session,
        reviewer,
        performer.id,
        provider="manual",
        provider_reference="strict-serving-age",
        status=AgeVerificationStatus.verified,
        country_code="PT",
        required_minimum_age=18,
        achieved_assurance_level=AgeAssuranceLevel.high,
        expires_at=expires_at,
        confirmed=True,
        reason="Reviewed strict performer age evidence",
    )
    assert await trust_safety.valid_verified_release_for_content(db_session, content.id)

    await content_service.approve(db_session, content, reviewer)
    assert await public_content_surface_eligible(db_session, content)
    assert await can_access_content(db_session, content, None)
    assert await can_access_asset(db_session, asset.id, None)

    release.revoked_at = datetime.now(UTC)
    await db_session.flush()
    assert not await public_content_surface_eligible(db_session, content)
    assert not await can_access_asset(db_session, asset.id, None)


@pytest.mark.asyncio
async def test_manual_performer_override_requires_confirmation_and_audits_reason(
    db_session, reviewed_pt_compliance_policy
):
    owner, _ = await approved_creator(db_session, "performer-review@example.com")
    performer = await performers.create_identity(
        db_session,
        owner,
        "review subject",
        country_code="PT",
    )

    with pytest.raises(performers.PerformerError, match="confirmation"):
        await performers.record_identity_verification(
            db_session,
            owner,
            performer.id,
            provider="manual",
            provider_reference="identity-denied",
            status=PerformerIdentityStatus.verified,
            country_code="PT",
            expires_at=None,
            confirmed=False,
            reason="Reviewed source evidence",
        )

    row = await performers.record_identity_verification(
        db_session,
        owner,
        performer.id,
        provider="manual",
        provider_reference="identity-confirmed",
        status=PerformerIdentityStatus.verified,
        country_code="PT",
        expires_at=None,
        confirmed=True,
        reason="Reviewed government identity evidence",
    )
    assert row.metadata_json["review_reason"] == "Reviewed government identity evidence"
    event = await db_session.scalar(
        select(AuditEvent)
        .where(AuditEvent.event_type == "performer.identity_verification_recorded")
        .order_by(AuditEvent.created_at.desc())
    )
    assert event is not None
    assert event.metadata_json["confirmed"] is True
    assert event.metadata_json["reason"] == "Reviewed government identity evidence"


@pytest.mark.asyncio
async def test_performer_age_outcome_and_current_stronger_policy_fail_closed(
    db_session, reviewed_pt_compliance_policy
):
    owner, profile = await approved_creator(db_session, "performer-age-policy@example.com")
    reviewer, _ = await accounts.register(
        db_session,
        "performer-age-policy-reviewer@example.com",
        "strong-password-123",
        None,
        country_code="PT",
    )
    performer = await performers.create_identity(
        db_session,
        owner,
        "age policy subject",
        country_code="PT",
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Current performer policy",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.free,
    )
    db_session.add(content)
    await db_session.flush()
    await strict_performer_policy(
        db_session,
        minimum_age=18,
        required_assurance_level=AgeAssuranceLevel.medium,
        reverify_after_days=30,
    )
    release = await trust_safety.submit_consent_release(
        db_session,
        profile,
        owner,
        trust_safety.ConsentReleaseType.co_performer_release,
        performer.safe_reference,
        [content.id],
    )
    await trust_safety.verify_consent_release(db_session, release, reviewer, True)
    await performers.link_content_performer(
        db_session,
        owner,
        content.id,
        performer.id,
        release.id,
    )
    await performers.record_identity_verification(
        db_session,
        owner,
        performer.id,
        provider="manual",
        provider_reference="age-policy-identity",
        status=PerformerIdentityStatus.verified,
        country_code="PT",
        expires_at=datetime.now(UTC) + timedelta(days=31),
        confirmed=True,
        reason="Reviewed performer identity evidence",
    )

    with pytest.raises(performers.PerformerError, match="assurance"):
        await performers.record_age_verification(
            db_session,
            owner,
            performer.id,
            provider="manual",
            provider_reference="age-policy-insufficient",
            status=AgeVerificationStatus.verified,
            country_code="PT",
            required_minimum_age=18,
            achieved_assurance_level=AgeAssuranceLevel.none,
            expires_at=datetime.now(UTC) + timedelta(days=30),
            confirmed=True,
            reason="Reviewed insufficient age evidence",
        )
    with pytest.raises(performers.PerformerError, match="finite"):
        await performers.record_age_verification(
            db_session,
            owner,
            performer.id,
            provider="manual",
            provider_reference="age-policy-no-expiry",
            status=AgeVerificationStatus.verified,
            country_code="PT",
            required_minimum_age=18,
            achieved_assurance_level=AgeAssuranceLevel.medium,
            expires_at=None,
            confirmed=True,
            reason="Reviewed age evidence without expiry",
        )
    age = await performers.record_age_verification(
        db_session,
        owner,
        performer.id,
        provider="manual",
        provider_reference="age-policy-current",
        status=AgeVerificationStatus.verified,
        country_code="PT",
        required_minimum_age=18,
        achieved_assurance_level=AgeAssuranceLevel.medium,
        expires_at=datetime.now(UTC) + timedelta(days=30),
        confirmed=True,
        reason="Reviewed current performer age evidence",
    )
    assert age.metadata_json["verified_minimum_age_threshold"] == 18
    assert await trust_safety.valid_verified_release_for_content(db_session, content.id)

    await strict_performer_policy(
        db_session,
        minimum_age=21,
        required_assurance_level=AgeAssuranceLevel.high,
        reverify_after_days=30,
    )
    assert not await trust_safety.valid_verified_release_for_content(db_session, content.id)


@pytest.mark.asyncio
async def test_linked_performer_enforcement_is_independent_of_legacy_content_flag(
    db_session, reviewed_pt_compliance_policy
):
    owner, profile = await approved_creator(db_session, "performer-access@example.com")
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Linked performer content",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.free,
        requires_verified_consent=False,
    )
    performer = PerformerIdentity(
        owner_creator_id=profile.id,
        safe_reference="linked performer",
        country_code="PT",
        created_by_user_id=owner.id,
    )
    db_session.add_all([content, performer])
    await db_session.flush()
    db_session.add(
        VerifiedContentPerformer(
            content_id=content.id,
            performer_id=performer.id,
            identity_verification_required=True,
            age_verification_required=False,
            release_required=False,
        )
    )
    await db_session.flush()

    assert not await public_content_surface_eligible(db_session, content)


@pytest.mark.asyncio
async def test_every_performer_needs_current_records_and_one_release_cannot_cover_two(
    db_session, reviewed_pt_compliance_policy
):
    owner, profile = await approved_creator(db_session, "performer-cardinality@example.com")
    reviewer, _ = await accounts.register(
        db_session,
        "performer-cardinality-reviewer@example.com",
        "strong-password-123",
        None,
        country_code="PT",
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Cardinality content",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.free,
    )
    first = PerformerIdentity(
        owner_creator_id=profile.id,
        safe_reference="first performer",
        country_code="PT",
        created_by_user_id=owner.id,
    )
    second = PerformerIdentity(
        owner_creator_id=profile.id,
        safe_reference="second performer",
        country_code="PT",
        created_by_user_id=owner.id,
    )
    db_session.add_all([content, first, second])
    await db_session.flush()
    release = await trust_safety.submit_consent_release(
        db_session,
        profile,
        owner,
        trust_safety.ConsentReleaseType.co_performer_release,
        first.safe_reference,
        [content.id],
    )
    await trust_safety.verify_consent_release(db_session, release, reviewer, True)
    db_session.add_all(
        [
            VerifiedContentPerformer(
                content_id=content.id,
                performer_id=first.id,
                consent_release_id=release.id,
                identity_verification_required=False,
                age_verification_required=False,
                release_required=True,
            ),
            VerifiedContentPerformer(
                content_id=content.id,
                performer_id=second.id,
                consent_release_id=release.id,
                identity_verification_required=False,
                age_verification_required=False,
                release_required=True,
            ),
        ]
    )
    await db_session.flush()
    assert not await trust_safety.valid_verified_release_for_content(db_session, content.id)
    assert await trust_safety.creator_performer_consent_issue_count(db_session, profile.id) == 1

    await db_session.execute(
        VerifiedContentPerformer.__table__.delete().where(
            VerifiedContentPerformer.content_id == content.id
        )
    )
    link = VerifiedContentPerformer(
        content_id=content.id,
        performer_id=first.id,
        identity_verification_required=True,
        age_verification_required=True,
        release_required=False,
    )
    identity = PerformerIdentityVerification(
        performer_id=first.id,
        provider="test",
        provider_reference="current-identity",
        status=PerformerIdentityStatus.verified,
        country_code="PT",
        verified_at=datetime.now(UTC),
    )
    age = PerformerAgeVerification(
        performer_id=first.id,
        provider="test",
        provider_reference="current-age",
        status=AgeVerificationStatus.verified,
        country_code="PT",
        required_minimum_age=18,
        achieved_assurance_level=AgeAssuranceLevel.high,
        verified_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db_session.add_all([link, identity, age])
    await db_session.flush()
    assert await trust_safety.valid_verified_release_for_content(db_session, content.id)
    assert await trust_safety.creator_performer_consent_issue_count(db_session, profile.id) == 0
    age.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()
    assert not await trust_safety.valid_verified_release_for_content(db_session, content.id)
    assert await trust_safety.creator_performer_consent_issue_count(db_session, profile.id) == 1
    age.expires_at = datetime.now(UTC) + timedelta(days=1)
    identity.revoked_at = datetime.now(UTC)
    await db_session.flush()
    assert not await trust_safety.valid_verified_release_for_content(db_session, content.id)
