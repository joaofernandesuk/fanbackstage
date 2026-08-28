from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from app.accounts.service import ensure_roles
from app.audit.service import record_event
from app.core.config import Settings
from app.legal import service
from app.models.compliance import CountryRegistry
from app.models.identity import Role, User
from app.models.legal import (
    LegalAudience,
    LegalDocumentStatus,
    LegalDocumentType,
    LegalDocumentVersion,
    SiteSettingsVersion,
)
from app.models.notification import NotificationIntent
from app.schemas.legal import LegalAcceptanceInput, LinkBlock


async def actor(db_session, email: str = "legal-admin@example.com") -> User:
    await ensure_roles(db_session)
    role = await db_session.scalar(select(Role).where(Role.name == "super_admin"))
    user = User(email=email, password_hash="test", roles=[role], country_code="PT")
    db_session.add(user)
    await db_session.flush()
    return user


async def ensure_country(db_session, code: str = "PT") -> None:
    if not await db_session.get(CountryRegistry, code):
        names = {"PT": "Portugal", "US": "United States"}
        db_session.add(CountryRegistry(code=code, name=names[code], enabled=True))
        await db_session.flush()


def draft_values(**overrides):
    return {
        "document_type": LegalDocumentType.terms,
        "slug": "terms",
        "jurisdiction_code": None,
        "language": "en",
        "audience": LegalAudience.all_users,
        "title": "Terms",
        "body": [{"type": "paragraph", "text": "Reviewed terms."}],
        "requires_acceptance": True,
        "requires_legal_review": False,
        "approved_for_publication": True,
        "is_demo": False,
        **overrides,
    }


async def test_published_body_is_immutable_and_changes_use_a_new_version(db_session):
    await ensure_country(db_session)
    admin = await actor(db_session)
    document, published = await service.create_document(db_session, admin, draft_values())
    await service.publish_version(
        db_session,
        admin,
        published.id,
        reason="Reviewed launch terms",
        now=datetime(2026, 8, 27, tzinfo=UTC),
    )
    original_body = list(published.body_json)

    with pytest.raises(service.LegalError, match="immutable"):
        await service.update_draft(
            db_session,
            admin,
            published.id,
            {"body": [{"type": "paragraph", "text": "Changed in place."}]},
        )

    _, draft = await service.create_version(
        db_session,
        admin,
        document.id,
        draft_values(title="Terms update", body=[{"type": "paragraph", "text": "Next."}]),
    )
    assert draft.version == 2
    assert draft.status is LegalDocumentStatus.draft
    assert published.body_json == original_body


async def test_demo_text_cannot_publish_in_production(db_session, monkeypatch):
    await ensure_country(db_session)
    admin = await actor(db_session)
    _, draft = await service.create_document(
        db_session,
        admin,
        draft_values(slug="demo-terms", is_demo=True),
    )
    monkeypatch.setattr(service, "get_settings", lambda: Settings(environment="production"))

    with pytest.raises(service.LegalError, match="Demo legal text"):
        await service.publish_version(
            db_session,
            admin,
            draft.id,
            reason="Attempted production publication",
        )


async def test_site_settings_updates_are_append_only_versions(db_session):
    await ensure_country(db_session)
    admin = await actor(db_session)
    first = await service.update_site_settings(
        db_session,
        admin,
        {
            "support_email": "support@example.com",
            "footer_text": "First footer",
            "public_contact_text": None,
            "social_links": [],
            "homepage_announcement": "First notice",
            "maintenance_notice": None,
            "banner_level": "info",
            "banner_starts_at": None,
            "banner_ends_at": None,
            "reason": "Initial public settings",
        },
    )
    second = await service.update_site_settings(
        db_session,
        admin,
        {
            "support_email": "help@example.com",
            "footer_text": "Second footer",
            "public_contact_text": None,
            "social_links": [{"label": "Social", "url": "https://example.com/social"}],
            "homepage_announcement": None,
            "maintenance_notice": "Maintenance window",
            "banner_level": "warning",
            "banner_starts_at": None,
            "banner_ends_at": None,
            "reason": "Update support and maintenance",
        },
    )

    rows = list(
        await db_session.scalars(select(SiteSettingsVersion).order_by(SiteSettingsVersion.version))
    )
    assert (first.version, second.version) == (1, 2)
    assert [(row.version, row.is_current) for row in rows] == [(1, False), (2, True)]
    assert rows[0].footer_text == "First footer"
    assert rows[1].footer_text == "Second footer"
    with pytest.raises(DBAPIError, match="append-only"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE site_settings_versions SET footer_text = 'tampered' WHERE id = :id"),
                {"id": rows[0].id},
            )
    with pytest.raises(DBAPIError, match="append-only"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("DELETE FROM site_settings_versions WHERE id = :id"),
                {"id": rows[1].id},
            )


def test_public_acceptance_payload_cannot_claim_registration_or_country():
    with pytest.raises(ValidationError):
        LegalAcceptanceInput.model_validate(
            {"version_ids": ["00000000-0000-0000-0000-000000000001"], "source": "registration"}
        )
    with pytest.raises(ValidationError):
        LegalAcceptanceInput.model_validate(
            {
                "version_ids": ["00000000-0000-0000-0000-000000000001"],
                "source": "account",
                "jurisdiction_code": "US",
            }
        )


def test_legal_link_schema_rejects_ambiguous_or_credential_bearing_urls():
    for href in ("/\\attacker.example", "https://user:secret@example.com"):
        with pytest.raises(ValidationError):
            LinkBlock.model_validate({"type": "link", "text": "Unsafe", "href": href})


async def test_published_version_rows_retain_exact_body_after_retirement(db_session):
    await ensure_country(db_session)
    admin = await actor(db_session)
    _, version = await service.create_document(db_session, admin, draft_values())
    await service.publish_version(
        db_session, admin, version.id, reason="Reviewed terms publication"
    )
    body = list(version.body_json)
    await service.retire_version(
        db_session, admin, version.id, reason="Superseded by reviewed terms"
    )
    persisted = await db_session.get(LegalDocumentVersion, version.id)
    assert persisted is not None
    assert persisted.status is LegalDocumentStatus.retired
    assert persisted.body_json == body


async def test_database_rejects_published_legal_mutation_and_deletion(db_session):
    await ensure_country(db_session)
    admin = await actor(db_session)
    document, version = await service.create_document(db_session, admin, draft_values())
    await service.publish_version(
        db_session,
        admin,
        version.id,
        reason="Reviewed immutable publication",
    )
    await db_session.flush()

    with pytest.raises(DBAPIError, match="immutable"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE legal_document_versions SET title = 'tampered' WHERE id = :id"),
                {"id": version.id},
            )
    with pytest.raises(DBAPIError, match="immutable"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("DELETE FROM legal_document_versions WHERE id = :id"),
                {"id": version.id},
            )
    with pytest.raises(DBAPIError, match="immutable"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE legal_documents SET slug = 'tampered' WHERE id = :id"),
                {"id": document.id},
            )
    with pytest.raises(DBAPIError, match="immutable"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("DELETE FROM legal_documents WHERE id = :id"),
                {"id": document.id},
            )


async def test_database_rejects_audit_event_mutation_and_deletion(db_session):
    admin = await actor(db_session)
    event = await record_event(
        db_session,
        "legal.immutable_audit_test",
        actor_user_id=admin.id,
        target_type="legal_document",
        target_id="immutable-test",
        correlation_id="immutable-audit",
        metadata={"reason": "original evidence"},
    )
    await db_session.flush()

    with pytest.raises(DBAPIError, match="append-only"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE audit_events SET event_type = 'tampered' WHERE id = :id"),
                {"id": event.id},
            )
    with pytest.raises(DBAPIError, match="append-only"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("DELETE FROM audit_events WHERE id = :id"),
                {"id": event.id},
            )
    await db_session.delete(admin)
    await db_session.flush()
    persisted = await db_session.get(type(event), event.id, populate_existing=True)
    assert persisted is not None
    assert persisted.actor_user_id is None
    assert persisted.event_type == "legal.immutable_audit_test"
    assert persisted.metadata_json == {"reason": "original evidence"}


async def test_database_rejects_legal_acceptance_mutation_and_deletion(db_session):
    await ensure_country(db_session)
    admin = await actor(db_session)
    fan = await actor(db_session, "legal-evidence-fan@example.com")
    _, version = await service.create_document(db_session, admin, draft_values())
    await service.publish_version(
        db_session,
        admin,
        version.id,
        reason="Reviewed acceptance evidence publication",
    )
    [acceptance] = await service.record_acceptances(
        db_session,
        fan,
        [version.id],
        source="account",
        jurisdiction_code="PT",
        correlation_id="immutable-acceptance",
    )
    await db_session.flush()

    with pytest.raises(DBAPIError, match="immutable"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE legal_acceptances SET source = 'tampered' WHERE id = :id"),
                {"id": acceptance.acceptance_id},
            )
    with pytest.raises(DBAPIError, match="immutable"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("DELETE FROM legal_acceptances WHERE id = :id"),
                {"id": acceptance.acceptance_id},
            )


async def test_production_readiness_requires_reviewed_non_demo_baseline(db_session):
    await ensure_country(db_session)
    admin = await actor(db_session)
    production = Settings(environment="production")

    ready, reasons = await service.production_legal_readiness(db_session, settings=production)
    assert ready is False
    assert set(reasons) == {
        "MISSING_REQUIRED_LEGAL_TERMS",
        "MISSING_REQUIRED_LEGAL_PRIVACY",
        "MISSING_REQUIRED_LEGAL_AGE_POLICY",
    }

    effective = datetime(2026, 8, 27, tzinfo=UTC)
    for document_type in service.REQUIRED_PRODUCTION_LEGAL_TYPES:
        _, version = await service.create_document(
            db_session,
            admin,
            draft_values(
                document_type=document_type,
                slug=document_type.value.replace("_", "-"),
                title=document_type.value,
            ),
        )
        await service.publish_version(
            db_session,
            admin,
            version.id,
            reason="Reviewed production baseline",
            now=effective,
        )

    ready, reasons = await service.production_legal_readiness(
        db_session,
        settings=production,
        now=effective,
    )
    assert ready is True
    assert reasons == ()


async def test_production_readiness_blocks_an_active_demo_legal_version(db_session):
    await ensure_country(db_session)
    admin = await actor(db_session)
    _, version = await service.create_document(
        db_session,
        admin,
        draft_values(slug="demo-terms", is_demo=True),
    )
    effective = datetime(2026, 8, 27, tzinfo=UTC)
    await service.publish_version(
        db_session,
        admin,
        version.id,
        reason="Development-only demo publication",
        now=effective,
    )

    ready, reasons = await service.production_legal_readiness(
        db_session,
        settings=Settings(environment="production"),
        now=effective,
    )
    assert ready is False
    assert "ACTIVE_DEMO_LEGAL_VERSION" in reasons


async def test_mandatory_publication_notifies_only_applicable_verified_audience(
    db_session,
):
    await ensure_country(db_session)
    await ensure_country(db_session, "US")
    admin = await actor(db_session)
    creator_role = await db_session.scalar(select(Role).where(Role.name == "creator"))
    viewer_role = await db_session.scalar(select(Role).where(Role.name == "viewer"))
    assert creator_role is not None and viewer_role is not None
    current = datetime(2026, 8, 27, tzinfo=UTC)
    applicable = User(
        email="applicable-creator@example.com",
        password_hash="test",
        roles=[viewer_role, creator_role],
        country_code="PT",
        email_verified_at=current,
    )
    wrong_country = User(
        email="other-creator@example.com",
        password_hash="test",
        roles=[viewer_role, creator_role],
        country_code="US",
        email_verified_at=current,
    )
    wrong_audience = User(
        email="fan-only@example.com",
        password_hash="test",
        roles=[viewer_role],
        country_code="PT",
        email_verified_at=current,
    )
    db_session.add_all([applicable, wrong_country, wrong_audience])
    await db_session.flush()

    _, version = await service.create_document(
        db_session,
        admin,
        draft_values(
            document_type=LegalDocumentType.creator_agreement,
            slug="creator-agreement-pt",
            title="Creator agreement",
            jurisdiction_code="PT",
            audience=LegalAudience.creator,
        ),
    )
    await service.publish_version(
        db_session,
        admin,
        version.id,
        reason="Reviewed creator agreement publication",
        now=current,
    )

    intents = (await db_session.scalars(select(NotificationIntent))).all()
    assert len(intents) == 1
    assert intents[0].recipient_user_id == applicable.id
    assert intents[0].notification_type == "LEGAL_ACCEPTANCE_REQUIRED"
    assert intents[0].source_domain == "legal"
    assert intents[0].source_id == str(version.id)
    assert "provider" not in str(intents[0].payload_json).lower()


async def test_future_mandatory_publication_notifies_at_effective_time_once(db_session):
    await ensure_country(db_session)
    admin = await actor(db_session)
    viewer_role = await db_session.scalar(select(Role).where(Role.name == "viewer"))
    assert viewer_role is not None
    current = datetime(2026, 8, 27, tzinfo=UTC)
    fan = User(
        email="future-legal-fan@example.com",
        password_hash="test",
        roles=[viewer_role],
        country_code="PT",
        email_verified_at=current,
    )
    db_session.add(fan)
    await db_session.flush()
    effective = current + timedelta(days=1)
    _, version = await service.create_document(
        db_session,
        admin,
        draft_values(
            slug="future-fan-terms",
            audience=LegalAudience.fan,
            effective_from=effective,
        ),
    )
    await service.publish_version(
        db_session,
        admin,
        version.id,
        reason="Schedule reviewed future fan terms",
        now=current,
    )
    assert await db_session.scalar(select(func.count(NotificationIntent.id))) == 0
    assert await service.notify_due_legal_acceptance_versions(db_session, now=current) == 0

    notified = await service.notify_due_legal_acceptance_versions(
        db_session,
        now=effective + timedelta(seconds=1),
    )
    assert notified == 1
    intent = await db_session.scalar(select(NotificationIntent))
    assert intent is not None
    assert intent.recipient_user_id == fan.id
    assert intent.source_id == str(version.id)
    assert (
        await service.notify_due_legal_acceptance_versions(
            db_session,
            now=effective + timedelta(seconds=2),
        )
        == 0
    )


async def test_due_legal_notifications_do_not_starve_versions_after_a_fixed_window(db_session):
    await ensure_country(db_session)
    admin = await actor(db_session)
    viewer_role = await db_session.scalar(select(Role).where(Role.name == "viewer"))
    assert viewer_role is not None
    current = datetime(2026, 8, 27, tzinfo=UTC)
    fan = User(
        email="later-legal-fan@example.com",
        password_hash="test",
        roles=[viewer_role],
        country_code="PT",
        email_verified_at=current,
    )
    db_session.add(fan)
    await db_session.flush()
    effective = current + timedelta(days=1)

    # These inapplicable versions deliberately occupy the old 50-row leading
    # window. The applicable fan version sorts after them and must still run.
    for index in range(50):
        _, version = await service.create_document(
            db_session,
            admin,
            draft_values(
                slug=f"creator-policy-{index:02d}",
                audience=LegalAudience.creator,
                effective_from=effective + timedelta(seconds=index),
            ),
        )
        await service.publish_version(
            db_session,
            admin,
            version.id,
            reason="Schedule reviewed creator policy",
            now=current,
        )

    _, fan_version = await service.create_document(
        db_session,
        admin,
        draft_values(
            slug="later-fan-policy",
            audience=LegalAudience.fan,
            effective_from=effective + timedelta(seconds=100),
        ),
    )
    await service.publish_version(
        db_session,
        admin,
        fan_version.id,
        reason="Schedule reviewed fan policy after the old window",
        now=current,
    )

    assert (
        await service.notify_due_legal_acceptance_versions(
            db_session,
            now=effective + timedelta(seconds=200),
        )
        == 1
    )
    intent = await db_session.scalar(select(NotificationIntent))
    assert intent is not None
    assert intent.recipient_user_id == fan.id
    assert intent.source_id == str(fan_version.id)
