from datetime import UTC, datetime

import httpx
import pytest
from fastapi import HTTPException, Request
from sqlalchemy import func, select

from app.accounts import service as account_service
from app.accounts.service import ensure_roles
from app.api.routes.legal import public_legal_documents
from app.core.config import get_settings
from app.legal import service
from app.main import app
from app.models.compliance import CountryRegistry
from app.models.creator import CreatorProfile
from app.models.identity import Role, User
from app.models.legal import (
    LegalAcceptance,
    LegalAudience,
    LegalDocumentType,
)
from app.models.referral import AffiliatePartner, AffiliatePartnerStatus

NOW = datetime(2026, 8, 27, tzinfo=UTC)


async def setup_people(db_session) -> tuple[User, User]:
    if not await db_session.get(CountryRegistry, "PT"):
        db_session.add(CountryRegistry(code="PT", name="Portugal", enabled=True))
    await ensure_roles(db_session)
    await db_session.flush()
    super_admin = await db_session.scalar(select(Role).where(Role.name == "super_admin"))
    viewer = await db_session.scalar(select(Role).where(Role.name == "viewer"))
    admin = User(
        email="legal-owner@example.com",
        password_hash="test",
        roles=[super_admin],
        country_code="PT",
    )
    fan = User(
        email="legal-fan@example.com",
        password_hash="test",
        roles=[viewer],
        country_code="PT",
    )
    db_session.add_all([admin, fan])
    await db_session.flush()
    return admin, fan


async def publish(
    db_session,
    admin: User,
    *,
    slug: str,
    title: str,
    audience: LegalAudience,
    jurisdiction_code: str | None = None,
):
    document, version = await service.create_document(
        db_session,
        admin,
        {
            "document_type": LegalDocumentType.fan_terms,
            "slug": slug,
            "jurisdiction_code": jurisdiction_code,
            "language": "en",
            "audience": audience,
            "title": title,
            "body": [{"type": "paragraph", "text": title}],
            "requires_acceptance": True,
            "requires_legal_review": False,
            "approved_for_publication": True,
            "is_demo": False,
        },
    )
    await service.publish_version(
        db_session,
        admin,
        version.id,
        reason="Reviewed legal publication",
        now=NOW,
    )
    return document, version


async def test_resolver_prefers_country_and_role_specific_versions(db_session):
    admin, _ = await setup_people(db_session)
    _, global_version = await publish(
        db_session,
        admin,
        slug="fan-terms",
        title="Global fan terms",
        audience=LegalAudience.fan,
    )
    _, country_version = await publish(
        db_session,
        admin,
        slug="fan-terms",
        title="Portugal fan terms",
        audience=LegalAudience.fan,
        jurisdiction_code="PT",
    )

    portugal = await service.resolve_document(
        db_session,
        "fan-terms",
        jurisdiction_code="PT",
        audiences=(LegalAudience.fan, LegalAudience.all_users),
        now=NOW,
    )
    global_scope = await service.resolve_document(
        db_session,
        "fan-terms",
        jurisdiction_code="US",
        audiences=(LegalAudience.fan, LegalAudience.all_users),
        now=NOW,
    )
    assert portugal and portugal.version_id == country_version.id
    assert global_scope and global_scope.version_id == global_version.id


async def test_registration_requires_exact_set_and_records_exact_versions(db_session):
    admin, fan = await setup_people(db_session)
    _, global_version = await publish(
        db_session,
        admin,
        slug="terms",
        title="Platform terms",
        audience=LegalAudience.all_users,
    )
    _, fan_version = await publish(
        db_session,
        admin,
        slug="fan-terms",
        title="Fan terms",
        audience=LegalAudience.fan,
        jurisdiction_code="PT",
    )
    expected = [global_version.id, fan_version.id]

    with pytest.raises(service.LegalError, match="current required"):
        await service.validate_registration_acceptances(
            db_session,
            [global_version.id],
            jurisdiction_code="PT",
            now=NOW,
        )

    validated = await service.validate_registration_acceptances(
        db_session,
        expected,
        jurisdiction_code="PT",
        now=NOW,
    )
    assert {item.version_id for item in validated} == set(expected)
    accepted = await service.record_registration_acceptances(
        db_session,
        fan,
        expected,
        jurisdiction_code="PT",
        correlation_id="registration-test",
        now=NOW,
    )
    replay = await service.record_registration_acceptances(
        db_session,
        fan,
        expected,
        jurisdiction_code="PT",
        correlation_id="registration-replay",
        now=NOW,
    )
    assert {item.version_id for item in accepted} == set(expected)
    assert [item.acceptance_id for item in replay] == [item.acceptance_id for item in accepted]
    assert await db_session.scalar(select(func.count(LegalAcceptance.id))) == 2


async def test_old_global_version_cannot_be_accepted_when_country_version_applies(db_session):
    admin, fan = await setup_people(db_session)
    _, global_version = await publish(
        db_session,
        admin,
        slug="terms",
        title="Global terms",
        audience=LegalAudience.all_users,
    )
    await publish(
        db_session,
        admin,
        slug="terms",
        title="Portugal terms",
        audience=LegalAudience.all_users,
        jurisdiction_code="PT",
    )

    with pytest.raises(service.LegalError, match="more specific"):
        await service.record_acceptances(
            db_session,
            fan,
            [global_version.id],
            source="account",
            jurisdiction_code="PT",
            correlation_id="wrong-scope",
            now=NOW,
        )


async def test_active_affiliate_owner_receives_affiliate_legal_audience(db_session):
    _, fan = await setup_people(db_session)
    db_session.add(
        AffiliatePartner(
            public_id="affiliate-legal-owner",
            name="Affiliate Legal Owner",
            status=AffiliatePartnerStatus.active,
            owner_user_id=fan.id,
        )
    )
    await db_session.flush()

    audiences = await service.audiences_for_user(db_session, fan)
    assert LegalAudience.affiliate in audiences
    assert LegalAudience.fan in audiences


async def test_authenticated_public_footer_resolution_uses_account_country(db_session):
    admin, fan = await setup_people(db_session)
    _, portugal = await publish(
        db_session,
        admin,
        slug="footer-terms",
        title="Portugal footer terms",
        audience=LegalAudience.fan,
        jurisdiction_code="PT",
    )
    request = Request({"type": "http", "method": "GET", "path": "/", "headers": [], "client": None})

    documents = await public_legal_documents(
        db=db_session,
        identity=(fan, object()),
        request=request,
        jurisdiction_code=None,
        language="en",
    )
    assert [document.version_id for document in documents] == [portugal.id]

    with pytest.raises(HTTPException) as conflict:
        await public_legal_documents(
            db=db_session,
            identity=(fan, object()),
            request=request,
            jurisdiction_code="US",
            language="en",
        )
    assert conflict.value.status_code == 409


async def test_successor_legal_version_blocks_product_api_but_not_recovery_routes(db_session):
    admin, fan = await setup_people(db_session)
    admin.email_verified_at = NOW
    fan.email_verified_at = NOW
    document, first = await publish(
        db_session,
        admin,
        slug="reaccept-terms",
        title="Initial terms",
        audience=LegalAudience.all_users,
    )
    await service.record_acceptances(
        db_session,
        fan,
        [first.id],
        source="account",
        jurisdiction_code="PT",
        correlation_id="initial-acceptance",
        now=NOW,
    )
    raw_session = await account_service.create_session(
        db_session,
        fan,
        "legal-gate-login",
        "test-client",
    )
    admin_session = await account_service.create_session(
        db_session,
        admin,
        "legal-operator-gate-login",
        "test-client",
    )
    await db_session.commit()

    _, successor = await service.create_version(
        db_session,
        admin,
        document.id,
        {
            "title": "Updated terms",
            "body": [{"type": "paragraph", "text": "Reviewed updated terms."}],
            "requires_acceptance": True,
            "requires_legal_review": False,
            "approved_for_publication": True,
            "is_demo": False,
        },
    )
    await service.publish_version(
        db_session,
        admin,
        successor.id,
        reason="Reviewed successor publication",
        now=NOW,
    )
    await db_session.commit()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as admin_client:
        admin_client.cookies.set(get_settings().session_cookie_name, admin_session)
        blocked_operator = await admin_client.get("/api/v1/admin/compliance/templates")
        assert blocked_operator.status_code == 428
        assert blocked_operator.json()["detail"]["code"] == "LEGAL_ACCEPTANCE_REQUIRED"

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        client.cookies.set(get_settings().session_cookie_name, raw_session)
        bootstrap = await client.get("/api/v1/me")
        assert bootstrap.status_code == 200
        blocked = await client.post("/api/v1/creators/me/application")
        assert blocked.status_code == 428
        assert blocked.json()["detail"] == {
            "code": "LEGAL_ACCEPTANCE_REQUIRED",
            "action": "ACCEPT_LEGAL",
            "message": "Current legal terms must be accepted before continuing.",
            "reason": "Current legal terms must be accepted before continuing.",
            "version_ids": [str(successor.id)],
        }
        assert await db_session.scalar(select(func.count(CreatorProfile.id))) == 0
        legacy_age = await client.post(
            "/api/v1/auth/adult-access",
            json={"adult_confirmed": True},
        )
        assert legacy_age.status_code == 428
        await db_session.refresh(fan)
        assert fan.adult_attested_at is None
        assert fan.adult_attestation_version is None

        compliance = await client.get(
            "/api/v1/compliance/decision", params={"feature": "platform_access"}
        )
        assert compliance.status_code == 200
        assert compliance.json()["allowed"] is False
        assert compliance.json()["code"] == "LEGAL_ACCEPTANCE_REQUIRED"

        requirements = await client.get("/api/v1/legal/me/requirements")
        history = await client.get("/api/v1/legal/me/acceptances")
        public_copy = await client.get("/api/v1/legal/documents/reaccept-terms")
        assert requirements.status_code == history.status_code == public_copy.status_code == 200
        assert [item["version_id"] for item in requirements.json()["documents"]] == [
            str(successor.id)
        ]
        assert [item["version_id"] for item in history.json()] == [str(first.id)]

        accepted = await client.post(
            "/api/v1/legal/acceptances",
            json={"version_ids": [str(successor.id)], "source": "interstitial"},
        )
        assert accepted.status_code == 200
        assert (await client.post("/api/v1/creators/me/application")).status_code == 200
        assert await db_session.scalar(select(func.count(CreatorProfile.id))) == 1
        assert (
            await client.get("/api/v1/compliance/decision", params={"feature": "platform_access"})
        ).json()["allowed"] is True

        _, third = await service.create_version(
            db_session,
            admin,
            document.id,
            {
                "title": "Third terms",
                "body": [{"type": "paragraph", "text": "Third reviewed terms."}],
                "requires_acceptance": True,
                "requires_legal_review": False,
                "approved_for_publication": True,
                "is_demo": False,
            },
        )
        await service.publish_version(
            db_session,
            admin,
            third.id,
            reason="Reviewed third publication",
            now=NOW,
        )
        await db_session.commit()
        assert (await client.post("/api/v1/auth/logout")).status_code == 200
