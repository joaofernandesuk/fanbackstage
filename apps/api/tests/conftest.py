import os
from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

if "FANBACKSTAGE_DATABASE_URL" not in os.environ:
    raise RuntimeError("Integration tests require FANBACKSTAGE_DATABASE_URL pointing to PostgreSQL")
os.environ.setdefault("FANBACKSTAGE_ENVIRONMENT", "test")

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.compliance import (
    AgeAssuranceLevel,
    CompliancePolicyStatus,
    CompliancePolicyTemplate,
    CompliancePolicyTemplateRevision,
    JurisdictionPolicyRevision,
)
from app.models.identity import User


class _TrustedSelfAttestedAccounts:
    """Test factory adapter for scenarios whose users accepted the 18+ baseline."""

    async def register(self, db, email, password, correlation_id, **kwargs):
        from app.accounts import service

        kwargs.setdefault("adult_confirmed", True)
        # This adapter represents a trusted, established test identity.  New
        # production policy deliberately fails closed when an account has no
        # jurisdiction authority, so legacy feature fixtures must supply one.
        kwargs.setdefault("country_code", "PT")
        return await service.register(db, email, password, correlation_id, **kwargs)

    def __getattr__(self, name):
        from app.accounts import service

        return getattr(service, name)


trusted_self_attested_accounts = _TrustedSelfAttestedAccounts()


class _LiveKitControlRecorder:
    """Network-free provider control used by integration tests only."""

    def __init__(self) -> None:
        self.closed_rooms: list[str] = []
        self.removed_participants: list[tuple[str, str]] = []

    async def close_room(self, room_name: str) -> None:
        self.closed_rooms.append(room_name)

    async def remove_participant(self, room_name: str, identity: str) -> None:
        self.removed_participants.append((room_name, identity))


@pytest.fixture(autouse=True)
def livekit_control(monkeypatch) -> _LiveKitControlRecorder:
    """Prevent tests and demo fixtures from reaching a real LiveKit control plane."""

    from app.streaming import service as streaming_service

    recorder = _LiveKitControlRecorder()
    monkeypatch.setattr(streaming_service, "livekit_control_provider", lambda: recorder)
    return recorder


@pytest.fixture(autouse=True)
async def clean_database() -> None:
    async with SessionLocal() as session:
        await session.execute(
            text(
                "TRUNCATE live_provider_control_intents, "
                "staging_creator_kyc_sandbox_events, creator_kyc_webhook_events, "
                "staging_payment_sandbox_events, payment_webhook_events, "
                "legal_acceptances, legal_document_versions, legal_documents, "
                "site_settings_versions, verified_content_performers, "
                "performer_age_verifications, performer_identity_verifications, "
                "performer_identities, age_provider_callback_events, age_provider_probes, "
                "age_verification_records, anonymous_compliance_sessions, "
                "feature_flag_revisions, jurisdiction_policy_revisions, "
                "compliance_policy_template_revisions, compliance_policy_templates, "
                "feature_refunds, feature_bookings, feature_prices, feature_slots, "
                "feature_surfaces, discovery_events, discovery_hides, discovery_configs, "
                "audit_events, creator_social_links, creator_profile_languages, "
                "creator_profile_categories, creator_username_history, creator_status_history, "
                "creator_verifications, creator_profiles, creator_languages, creator_categories, "
                "security_tokens, user_sessions, user_roles, users, roles CASCADE"
            )
        )
        await session.execute(text("UPDATE country_registry SET enabled = true"))
        await session.commit()
    redis = Redis.from_url(get_settings().redis_url)
    await redis.flushdb()
    await redis.aclose()
    yield


@pytest.fixture
async def db_session():
    async with SessionLocal() as session:
        yield session


@pytest.fixture(autouse=True)
async def reviewed_pt_compliance_policy(request, clean_database) -> None:
    """Reviewed non-legal test policy for HTTP flows that need jurisdiction resolution."""

    if request.node.path.name == "test_demo_seed.py":
        return
    now = datetime.now(UTC)
    async with SessionLocal() as session:
        actor = User(
            email="compliance-policy-fixture@example.test",
            password_hash="test-fixture-not-authenticatable",
            country_code="PT",
        )
        session.add(actor)
        await session.flush()
        template = CompliancePolicyTemplate(
            key="test-baseline",
            name="Test baseline",
            description="Automated test policy; not a legal assertion.",
        )
        session.add(template)
        await session.flush()
        rules = {
            "enabled": True,
            "registration_allowed": True,
            "creator_registration_allowed": True,
            "purchases_allowed": True,
            "subscriptions_allowed": True,
            "ppv_allowed": True,
            "live_allowed": True,
            "marketplace_allowed": True,
            "featuring_allowed": True,
            "marketing_email_allowed": True,
            "messaging_allowed": True,
            "minimum_age": 18,
            "fan_age_verification_required": False,
            "anonymous_adult_preview_allowed": True,
            "required_assurance_level": AgeAssuranceLevel.self_attested.value,
            "reverify_after_days": None,
            "grace_period_days": 0,
            "creator_identity_required": False,
            "creator_age_verification_required": False,
            "payout_kyc_required": False,
            "co_performer_verification_required": False,
            "release_required": False,
            "explicit_public_preview_allowed": False,
            "restricted_media_policy": "test-restricted",
            "age_provider": "test",
            "provider_policy_key": None,
        }
        template_revision = CompliancePolicyTemplateRevision(
            template_id=template.id,
            version=1,
            status=CompliancePolicyStatus.active,
            rules_json=rules,
            is_demo=True,
            effective_from=now - timedelta(days=1),
            created_by_user_id=actor.id,
            reviewed_at=now,
            reviewed_by_user_id=actor.id,
            change_reason="Automated test fixture",
        )
        session.add(template_revision)
        await session.flush()
        session.add(
            JurisdictionPolicyRevision(
                country_code="PT",
                version=1,
                template_revision_id=template_revision.id,
                status=CompliancePolicyStatus.active,
                overrides_json={},
                is_demo=True,
                effective_from=now - timedelta(days=1),
                created_by_user_id=actor.id,
                reviewed_at=now,
                reviewed_by_user_id=actor.id,
                change_reason="Automated test fixture",
            )
        )
        await session.commit()
