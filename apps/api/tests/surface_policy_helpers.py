from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.compliance.policy import create_jurisdiction_revision, create_template_revision
from app.compliance.types import PolicyOverrides, PolicyRules
from app.models.compliance import CompliancePolicyStatus, CompliancePolicyTemplateRevision


async def publish_creator_identity_policy(db) -> None:
    """Publish an immutable reviewed successor for creator-containment tests."""

    revision = await db.scalar(
        select(CompliancePolicyTemplateRevision)
        .order_by(CompliancePolicyTemplateRevision.version.desc())
        .limit(1)
    )
    assert revision is not None
    assert revision.reviewed_by_user_id is not None
    now = datetime.now(UTC)
    rules = PolicyRules.model_validate(revision.rules_json).model_copy(
        update={
            "creator_identity_required": True,
            "reverify_after_days": 30,
        }
    )
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
        change_reason="Require current creator identity verification in surface test",
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
        change_reason="Apply current creator identity requirement in surface test",
        is_demo=True,
    )
