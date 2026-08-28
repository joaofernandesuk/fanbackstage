from datetime import UTC, datetime, timedelta

import pytest
from conftest import trusted_self_attested_accounts as accounts
from pydantic import ValidationError
from sqlalchemy import func, select

from app.api.routes import admin as admin_routes
from app.content.access import can_access_content
from app.creators import service as creators
from app.finance import service as finance
from app.models.audit import AuditEvent
from app.models.content import (
    AccessPolicy,
    ContentItem,
    ContentStatus,
    ContentType,
    ModerationStatus,
)
from app.models.creator import CreatorStatus
from app.models.finance import (
    LedgerAccount,
    LedgerAccountKind,
    LedgerDirection,
    LedgerEntry,
    LedgerTransaction,
    LedgerTransactionType,
)
from app.models.identity import Role, User
from app.models.referral import (
    ReferralActorType,
    ReferralCommissionAllocation,
    ReferralLinkStatus,
    ReferralProgramStatus,
    ReferralProgramType,
    ReferralSubscriptionRewardWindow,
    SignupAttribution,
)
from app.referrals import service as referrals
from app.schemas.referral import ReferralLinkInput, ReferralPolicyInput, ReferralProgramInput


async def approved_creator(db, email: str):
    user, _ = await accounts.register(db, email, "strong-password-123", None)
    profile = await creators.get_or_create_profile(db, user)
    await creators.update_profile(
        db, profile, {"username": email.split("@")[0], "display_name": "Creator"}, user.id
    )
    await creators.submit(db, profile, user.id)
    await creators.development_verify(db, profile, True, user.id)
    await creators.set_status(db, profile, CreatorStatus.approved, user.id)
    profile.is_public = True
    await db.flush()
    return user, profile


def test_referral_admin_changes_require_meaningful_reason_and_confirmation():
    common = {
        "actor_type": "platform_campaign",
        "program_type": "user_user_referral",
    }
    with pytest.raises(ValidationError):
        ReferralProgramInput(**common, reason="reviewed", confirmed=False)
    with pytest.raises(ValidationError):
        ReferralProgramInput(**common, reason="        ", confirmed=True)


@pytest.mark.asyncio
async def test_super_admin_referral_configuration_is_confirmed_reasoned_and_audited(db_session):
    super_admin = User(
        email="referral-audit-super-admin@example.test",
        password_hash="not-authenticatable",
        country_code="PT",
        roles=[Role(name="super_admin", description="Referral audit test role")],
    )
    beneficiary = User(
        email="referral-audit-beneficiary@example.test",
        password_hash="not-authenticatable",
        country_code="PT",
    )
    db_session.add_all([super_admin, beneficiary])
    await db_session.flush()
    identity = (super_admin, None)

    program_response = await admin_routes.create_referral_program(
        ReferralProgramInput(
            actor_type="user",
            program_type="user_user_referral",
            owner_user_id=beneficiary.id,
            reason="Approve a bounded fan referral programme",
            confirmed=True,
        ),
        identity,
        db_session,
    )
    program_id = program_response["id"]
    policy_response = await admin_routes.create_referral_policy(
        program_id,
        ReferralPolicyInput(
            basis_points=750,
            eligible_revenue_types=["ppv", "subscription"],
            attribution_window_days=30,
            subscription_reward_window_days=90,
            reason="Approve the initial commission policy",
            confirmed=True,
        ),
        identity,
        db_session,
    )
    await admin_routes.create_referral_link(
        program_id,
        ReferralLinkInput(
            policy_id=policy_response["id"],
            code="AUDIT-REFERRAL",
            destination_path="/discover",
            source="admin-test",
            reason="Activate the reviewed referral link",
            confirmed=True,
        ),
        identity,
        db_session,
    )

    events = {
        event.event_type: event
        for event in (
            await db_session.scalars(
                select(AuditEvent).where(AuditEvent.actor_user_id == super_admin.id)
            )
        ).all()
    }
    assert set(events) == {
        "referral.program_created",
        "referral.policy_created",
        "referral.link_created",
    }
    for event in events.values():
        assert event.actor_user_id == super_admin.id
        assert event.metadata_json["confirmed"] is True
        assert event.metadata_json["reason"]
        assert event.metadata_json["scope"]["program_id"] == str(program_id)
        assert event.metadata_json["before"] == "none"
        assert event.metadata_json["after"]
    assert events["referral.program_created"].metadata_json["after"]["status"] == "active"
    assert events["referral.policy_created"].metadata_json["after"]["basis_points"] == 750
    assert events["referral.link_created"].metadata_json["after"]["code"] == "AUDIT-REFERRAL"


@pytest.mark.asyncio
async def test_referral_link_is_internal_signed_and_signup_attribution_is_immutable(db_session):
    referrer, _ = await accounts.register(
        db_session, "referrer-phase10@example.com", "strong-password-123", None
    )
    program = await referrals.create_program(
        db_session,
        actor_type=ReferralActorType.user,
        program_type=ReferralProgramType.user_user_referral,
        owner_user_id=referrer.id,
    )
    policy = await referrals.create_policy(
        db_session,
        program,
        basis_points=2_500,
        eligible_revenue_types=["ppv", "subscription"],
    )
    link = await referrals.create_link(
        db_session, program, policy, code="Invite-Phase10", destination_path="/creator/example"
    )
    resolved, token = await referrals.resolve_click(
        db_session, "invite-phase10", "first-party-session", source="social", utm={"source": "x"}
    )
    assert resolved.id == link.id
    again, same_token = await referrals.resolve_click(
        db_session, "INVITE-PHASE10", "first-party-session", source="social"
    )
    assert again.id == link.id
    assert token == same_token
    attributed, _ = await accounts.register(
        db_session, "attributed-phase10@example.com", "strong-password-123", None
    )
    snapshot = await referrals.snapshot_signup_attribution(db_session, attributed, token)
    assert snapshot
    assert snapshot.policy_snapshot["commission_funding"] == "platform_commission"
    assert snapshot.policy_snapshot["attribution_window_days"] == 30
    assert snapshot.policy_snapshot["subscription_reward_window_days"] == 90
    assert await referrals.snapshot_signup_attribution(db_session, attributed, token) is None
    assert await db_session.scalar(
        select(SignupAttribution).where(SignupAttribution.user_id == attributed.id)
    )


@pytest.mark.asyncio
async def test_referral_safety_expiry_and_deferred_creator_creator_program(db_session):
    owner, _ = await accounts.register(
        db_session, "referral-safety-owner@example.com", "strong-password-123", None
    )
    program = await referrals.create_program(
        db_session,
        actor_type=ReferralActorType.user,
        program_type=ReferralProgramType.user_user_referral,
        owner_user_id=owner.id,
    )
    policy = await referrals.create_policy(
        db_session, program, basis_points=100, eligible_revenue_types=["ppv"]
    )
    with pytest.raises(referrals.ReferralError, match="internal path"):
        await referrals.create_link(
            db_session, program, policy, code="external", destination_path="https://evil.example"
        )
    link = await referrals.create_link(
        db_session,
        program,
        policy,
        code="expired",
        destination_path="/",
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    with pytest.raises(referrals.ReferralError, match="unavailable"):
        await referrals.resolve_click(db_session, link.code, "another-session")
    deferred = await referrals.create_program(
        db_session,
        actor_type=ReferralActorType.creator,
        program_type=ReferralProgramType.creator_creator_referral,
        owner_user_id=owner.id,
    )
    assert deferred.status is ReferralProgramStatus.paused
    link.status = ReferralLinkStatus.disabled


@pytest.mark.asyncio
async def test_ppv_referral_uses_only_platform_fee_and_refund_reverses_snapshot(db_session):
    referrer, _ = await accounts.register(
        db_session, "phase10-referrer@example.com", "strong-password-123", None
    )
    program = await referrals.create_program(
        db_session,
        actor_type=ReferralActorType.user,
        program_type=ReferralProgramType.user_user_referral,
        owner_user_id=referrer.id,
    )
    policy = await referrals.create_policy(
        db_session, program, basis_points=2_500, eligible_revenue_types=["ppv"]
    )
    await referrals.create_link(
        db_session, program, policy, code="PPV-PLATFORM-FEE", destination_path="/"
    )
    _, token = await referrals.resolve_click(db_session, "PPV-PLATFORM-FEE", "ppv-session")
    referred_buyer, _ = await accounts.register(
        db_session, "phase10-referred@example.com", "strong-password-123", None
    )
    assert await referrals.snapshot_signup_attribution(db_session, referred_buyer, token)
    ordinary_buyer, _ = await accounts.register(
        db_session, "phase10-ordinary@example.com", "strong-password-123", None
    )
    seller, creator = await approved_creator(db_session, "phase10-seller@example.com")
    content = ContentItem(
        owner_creator_id=creator.id,
        created_by_user_id=seller.id,
        content_type=ContentType.gallery,
        title="Referral PPV",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=1_000,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()

    async def settle(buyer, key: str):
        purchase = await finance.initiate_purchase(db_session, buyer, content.id, key)
        attempt = await db_session.get(finance.PaymentAttempt, purchase.payment_attempt_id)
        assert attempt
        payload, signature = finance.development_webhook_payload(attempt)
        settled = await finance.process_development_webhook(db_session, payload, signature)
        assert settled
        return settled

    referred = await settle(referred_buyer, "referred-ppv")
    ordinary = await settle(ordinary_buyer, "ordinary-ppv")
    assert await can_access_content(db_session, content, referred_buyer)

    async def credits(transaction_id):
        rows = await db_session.execute(
            select(LedgerAccount.kind, LedgerEntry.amount_minor)
            .join(LedgerEntry, LedgerEntry.ledger_account_id == LedgerAccount.id)
            .where(
                LedgerEntry.transaction_id == transaction_id,
                LedgerEntry.direction == LedgerDirection.credit,
            )
        )
        return {kind: amount for kind, amount in rows}

    referred_credits = await credits(referred.ledger_transaction_id)
    ordinary_credits = await credits(ordinary.ledger_transaction_id)
    assert (
        referred_credits[LedgerAccountKind.creator_pending]
        == ordinary_credits[LedgerAccountKind.creator_pending]
    )
    assert referred_credits[LedgerAccountKind.referrer_pending] == 50
    assert referred_credits[LedgerAccountKind.platform_revenue] == 150
    assert ordinary_credits[LedgerAccountKind.platform_revenue] == 200
    allocation = await db_session.scalar(
        select(ReferralCommissionAllocation).where(
            ReferralCommissionAllocation.source_ledger_transaction_id
            == referred.ledger_transaction_id
        )
    )
    assert allocation
    assert allocation.amount_minor == 50
    assert allocation.amount_minor <= allocation.platform_fee_minor
    assert allocation.policy_snapshot["basis_points"] == 2_500

    # A later policy version cannot alter the historical allocation snapshot.
    replacement = await referrals.create_policy(
        db_session, program, basis_points=9_000, eligible_revenue_types=["ppv"]
    )
    assert replacement.version == 2
    assert allocation.policy_snapshot["basis_points"] == 2_500
    await finance.refund_purchase(db_session, referred, seller, "buyer request")
    assert allocation.reversed_at
    balance = await db_session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0))
        .join(LedgerAccount, LedgerAccount.id == LedgerEntry.ledger_account_id)
        .where(
            LedgerAccount.kind == LedgerAccountKind.referrer_pending,
            LedgerAccount.owner_user_id == referrer.id,
            LedgerEntry.direction == LedgerDirection.credit,
        )
    )
    debits = await db_session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0))
        .join(LedgerAccount, LedgerAccount.id == LedgerEntry.ledger_account_id)
        .where(
            LedgerAccount.kind == LedgerAccountKind.referrer_pending,
            LedgerAccount.owner_user_id == referrer.id,
            LedgerEntry.direction == LedgerDirection.debit,
        )
    )
    assert balance == debits == 50


@pytest.mark.asyncio
async def test_subscription_window_is_timestamp_based_and_invalid_fee_policy_fails_closed(
    db_session,
):
    referrer, _ = await accounts.register(
        db_session, "phase10-window-referrer@example.com", "strong-password-123", None
    )
    program = await referrals.create_program(
        db_session,
        actor_type=ReferralActorType.user,
        program_type=ReferralProgramType.user_user_referral,
        owner_user_id=referrer.id,
    )
    policy = await referrals.create_policy(
        db_session,
        basis_points=2_500,
        eligible_revenue_types=["subscription"],
        program=program,
        subscription_reward_window_days=90,
    )
    await referrals.create_link(
        db_session, program, policy, code="SUB-WINDOW", destination_path="/"
    )
    _, token = await referrals.resolve_click(db_session, "SUB-WINDOW", "subscription-window")
    buyer, _ = await accounts.register(
        db_session, "phase10-window-buyer@example.com", "strong-password-123", None
    )
    attribution = await referrals.snapshot_signup_attribution(db_session, buyer, token)
    assert attribution
    start = datetime(2026, 8, 22, tzinfo=UTC)
    entries, allocation = await referrals.revenue_allocation(
        db_session,
        buyer_user_id=buyer.id,
        revenue_type="subscription",
        currency="EUR",
        platform_fee_minor=100,
        occurred_at=start,
    )
    assert entries and allocation and allocation["amount_minor"] == 25
    window = await db_session.scalar(
        select(ReferralSubscriptionRewardWindow).where(
            ReferralSubscriptionRewardWindow.signup_attribution_id == attribution.id
        )
    )
    assert window and window.reward_window_ends_at == start + timedelta(days=90)
    assert (
        await referrals.revenue_allocation(
            db_session,
            buyer_user_id=buyer.id,
            revenue_type="subscription",
            currency="EUR",
            platform_fee_minor=100,
            occurred_at=start + timedelta(days=89, hours=23),
        )
    )[1]
    assert (
        await referrals.revenue_allocation(
            db_session,
            buyer_user_id=buyer.id,
            revenue_type="subscription",
            currency="EUR",
            platform_fee_minor=100,
            occurred_at=start + timedelta(days=90),
        )
    ) == ([], None)
    attribution.policy_snapshot = {**attribution.policy_snapshot, "basis_points": 10_001}
    with pytest.raises(referrals.ReferralError, match="snapshot is invalid"):
        await referrals.revenue_allocation(
            db_session,
            buyer_user_id=buyer.id,
            revenue_type="subscription",
            currency="EUR",
            platform_fee_minor=100,
            occurred_at=start + timedelta(days=1),
        )


@pytest.mark.asyncio
async def test_self_referral_and_suspended_affiliate_cannot_generate_new_commission(db_session):
    creator_user, creator = await approved_creator(db_session, "phase10-self@example.com")
    creator_program = await referrals.create_program(
        db_session,
        actor_type=ReferralActorType.creator,
        program_type=ReferralProgramType.creator_buyer_referral,
        owner_creator_id=creator.id,
    )
    creator_policy = await referrals.create_policy(
        db_session, creator_program, basis_points=1_000, eligible_revenue_types=["ppv"]
    )
    await referrals.create_link(
        db_session, creator_program, creator_policy, code="SELF-CREATOR", destination_path="/"
    )
    _, token = await referrals.resolve_click(db_session, "SELF-CREATOR", "self-session")
    assert await referrals.snapshot_signup_attribution(db_session, creator_user, token) is None

    admin, _ = await accounts.register(
        db_session, "phase10-affiliate-admin@example.com", "strong-password-123", None
    )
    partner = await referrals.create_affiliate_partner(db_session, admin, name="Partner")
    program = await referrals.create_program(
        db_session,
        actor_type=ReferralActorType.affiliate_partner,
        program_type=ReferralProgramType.affiliate_referral,
        affiliate_partner_id=partner.id,
    )
    policy = await referrals.create_policy(
        db_session, program, basis_points=1_000, eligible_revenue_types=["ppv"]
    )
    await referrals.create_link(
        db_session, program, policy, code="PAUSED-PARTNER", destination_path="/"
    )
    _, token = await referrals.resolve_click(db_session, "PAUSED-PARTNER", "affiliate-session")
    buyer, _ = await accounts.register(
        db_session, "phase10-affiliate-buyer@example.com", "strong-password-123", None
    )
    assert await referrals.snapshot_signup_attribution(db_session, buyer, token)
    await referrals.set_affiliate_partner_status(
        db_session, admin, partner, referrals.AffiliatePartnerStatus.suspended
    )
    assert (
        await referrals.revenue_allocation(
            db_session,
            buyer_user_id=buyer.id,
            revenue_type="ppv",
            currency="EUR",
            platform_fee_minor=100,
            occurred_at=datetime.now(UTC),
        )
    ) == ([], None)


@pytest.mark.asyncio
async def test_last_eligible_touch_wins_and_first_touch_is_retained(db_session):
    referrer, _ = await accounts.register(
        db_session, "phase10-touch-owner@example.com", "strong-password-123", None
    )
    program = await referrals.create_program(
        db_session,
        actor_type=ReferralActorType.user,
        program_type=ReferralProgramType.user_user_referral,
        owner_user_id=referrer.id,
    )
    policy = await referrals.create_policy(
        db_session, program, basis_points=100, eligible_revenue_types=["ppv"]
    )
    first_link = await referrals.create_link(
        db_session, program, policy, code="FIRST-TOUCH", destination_path="/first"
    )
    _, first_token = await referrals.resolve_click(db_session, first_link.code, "touch-session")
    last_link = await referrals.create_link(
        db_session, program, policy, code="LAST-TOUCH", destination_path="/last"
    )
    await referrals.resolve_click(db_session, last_link.code, "touch-session")
    user, _ = await accounts.register(
        db_session, "phase10-touch-user@example.com", "strong-password-123", None
    )
    attribution = await referrals.snapshot_signup_attribution(db_session, user, first_token)
    assert attribution
    assert attribution.first_touch_id != attribution.last_touch_id
    assert attribution.effective_link_id == last_link.id
    # Normal internal navigation creates no new touch, so the established
    # signup attribution cannot be reassigned by later referral traffic.
    assert await referrals.snapshot_signup_attribution(db_session, user, first_token) is None


@pytest.mark.asyncio
async def test_attribution_window_qualifies_at_day_29_and_expires_at_day_31(
    db_session, monkeypatch
):
    base = datetime(2026, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(referrals, "now", lambda: base)
    owner, _ = await accounts.register(
        db_session, "phase10-window-owner@example.com", "strong-password-123", None
    )
    program = await referrals.create_program(
        db_session,
        actor_type=ReferralActorType.user,
        program_type=ReferralProgramType.user_user_referral,
        owner_user_id=owner.id,
    )
    policy = await referrals.create_policy(
        db_session,
        program,
        basis_points=100,
        eligible_revenue_types=["ppv"],
        attribution_window_days=30,
    )
    link = await referrals.create_link(
        db_session, program, policy, code="WINDOW-EDGE", destination_path="/"
    )
    _, token = await referrals.resolve_click(db_session, link.code, "window-edge-session")
    monkeypatch.setattr(referrals, "now", lambda: base + timedelta(days=29))
    eligible, _ = await accounts.register(
        db_session, "phase10-day29@example.com", "strong-password-123", None
    )
    assert await referrals.snapshot_signup_attribution(db_session, eligible, token)
    monkeypatch.setattr(referrals, "now", lambda: base + timedelta(days=31))
    expired, _ = await accounts.register(
        db_session, "phase10-day31@example.com", "strong-password-123", None
    )
    assert await referrals.snapshot_signup_attribution(db_session, expired, token) is None


@pytest.mark.asyncio
async def test_cross_affiliate_dashboard_isolation_and_suspension_history(db_session):
    affiliate_a, _ = await accounts.register(
        db_session, "affiliate-a@example.com", "strong-password-123", None
    )
    affiliate_b, _ = await accounts.register(
        db_session, "affiliate-b@example.com", "strong-password-123", None
    )

    async def allocation_for(owner, code: str):
        partner = await referrals.create_affiliate_partner(
            db_session, owner, name=code, owner_user_id=owner.id
        )
        program = await referrals.create_program(
            db_session,
            actor_type=ReferralActorType.affiliate_partner,
            program_type=ReferralProgramType.affiliate_referral,
            affiliate_partner_id=partner.id,
        )
        policy = await referrals.create_policy(
            db_session, program, basis_points=1_000, eligible_revenue_types=["ppv"]
        )
        await referrals.create_link(db_session, program, policy, code=code, destination_path="/")
        _, token = await referrals.resolve_click(db_session, code, f"{code}-session")
        buyer, _ = await accounts.register(
            db_session, f"{code.lower()}-buyer@example.com", "strong-password-123", None
        )
        assert await referrals.snapshot_signup_attribution(db_session, buyer, token)
        _, allocation = await referrals.revenue_allocation(
            db_session,
            buyer_user_id=buyer.id,
            revenue_type="ppv",
            currency="EUR",
            platform_fee_minor=100,
            occurred_at=datetime.now(UTC),
        )
        source = LedgerTransaction(
            transaction_type=LedgerTransactionType.ppv_purchase,
            currency="EUR",
            idempotency_key=f"source:{code}",
            reference=f"source:{code}",
            effective_at=datetime.now(UTC),
            metadata_json={},
        )
        db_session.add(source)
        await db_session.flush()
        row = await referrals.record_revenue_allocation(
            db_session, source_ledger_transaction_id=source.id, allocation=allocation
        )
        assert row
        return partner, buyer, row

    partner_a, buyer_a, allocation_a = await allocation_for(affiliate_a, "AFF-A")
    _, _, allocation_b = await allocation_for(affiliate_b, "AFF-B")
    assert [
        row.id
        for row in await referrals.affiliate_dashboard_allocations(db_session, affiliate_a.id)
    ] == [allocation_a.id]
    assert [
        row.id
        for row in await referrals.affiliate_dashboard_allocations(db_session, affiliate_b.id)
    ] == [allocation_b.id]
    await referrals.set_affiliate_partner_status(
        db_session, affiliate_a, partner_a, referrals.AffiliatePartnerStatus.suspended
    )
    assert [
        row.id
        for row in await referrals.affiliate_dashboard_allocations(db_session, affiliate_a.id)
    ] == [allocation_a.id]
    assert (
        await referrals.revenue_allocation(
            db_session,
            buyer_user_id=buyer_a.id,
            revenue_type="ppv",
            currency="EUR",
            platform_fee_minor=100,
            occurred_at=datetime.now(UTC),
        )
    ) == ([], None)


@pytest.mark.asyncio
async def test_dashboard_totals_reconcile_to_immutable_referral_ledger_not_current_policy(
    db_session,
):
    referrer, _ = await accounts.register(
        db_session, "phase10-dashboard-owner@example.com", "strong-password-123", None
    )
    program = await referrals.create_program(
        db_session,
        actor_type=ReferralActorType.user,
        program_type=ReferralProgramType.user_user_referral,
        owner_user_id=referrer.id,
    )
    policy = await referrals.create_policy(
        db_session, program, basis_points=1_000, eligible_revenue_types=["ppv"]
    )
    link = await referrals.create_link(
        db_session, program, policy, code="DASHBOARD-LEDGER", destination_path="/"
    )
    _, token = await referrals.resolve_click(db_session, link.code, "dashboard-ledger-session")
    buyer, _ = await accounts.register(
        db_session, "phase10-dashboard-buyer@example.com", "strong-password-123", None
    )
    assert await referrals.snapshot_signup_attribution(db_session, buyer, token)
    referral_entries, allocation_metadata = await referrals.revenue_allocation(
        db_session,
        buyer_user_id=buyer.id,
        revenue_type="ppv",
        currency="EUR",
        platform_fee_minor=100,
        occurred_at=datetime.now(UTC),
    )
    assert allocation_metadata and allocation_metadata["amount_minor"] == 10
    clearing = await finance._account(db_session, LedgerAccountKind.platform_clearing, "EUR")
    source = await finance.post_entries(
        db_session,
        transaction_type=LedgerTransactionType.ppv_purchase,
        currency="EUR",
        idempotency_key="dashboard-ledger-source",
        reference="dashboard-ledger-source",
        entries=[(clearing, LedgerDirection.debit, 10), *referral_entries],
    )
    allocation = await referrals.record_revenue_allocation(
        db_session,
        source_ledger_transaction_id=source.id,
        allocation=allocation_metadata,
    )
    assert allocation

    pending_dashboard = await referrals.dashboard(db_session, referrer.id)
    assert pending_dashboard["totals_by_currency"] == {
        "EUR": {
            "pending_amount_minor": 10,
            "available_amount_minor": 0,
            "reversed_amount_minor": 0,
        }
    }
    assert pending_dashboard["links"] == [
        {
            "public_id": link.public_id,
            "code": link.code,
            "destination_path": "/",
            "status": "active",
            "conversions": 1,
        }
    ]

    release_entries, released_allocation = await referrals.release_entries(db_session, source.id)
    assert released_allocation == allocation
    await finance.post_entries(
        db_session,
        transaction_type=LedgerTransactionType.earnings_release,
        currency="EUR",
        idempotency_key="dashboard-ledger-release",
        reference="dashboard-ledger-release",
        entries=release_entries,
        reversal_of_transaction_id=source.id,
    )
    allocation.released_at = datetime.now(UTC)

    # An attempted future policy edit cannot alter the snapshot-based dashboard.
    policy.basis_points = 9_000
    available_dashboard = await referrals.dashboard(db_session, referrer.id)
    assert available_dashboard["totals_by_currency"]["EUR"] == {
        "pending_amount_minor": 0,
        "available_amount_minor": 10,
        "reversed_amount_minor": 0,
    }
    available_balance = await db_session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0))
        .join(LedgerAccount, LedgerAccount.id == LedgerEntry.ledger_account_id)
        .where(
            LedgerAccount.owner_user_id == referrer.id,
            LedgerAccount.kind == LedgerAccountKind.referrer_available,
            LedgerEntry.direction == LedgerDirection.credit,
        )
    )
    assert (
        available_balance
        == available_dashboard["totals_by_currency"]["EUR"]["available_amount_minor"]
    )
