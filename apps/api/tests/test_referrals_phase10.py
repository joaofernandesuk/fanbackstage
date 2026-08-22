from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.accounts import service as accounts
from app.models.referral import (
    ReferralActorType,
    ReferralLinkStatus,
    ReferralProgramStatus,
    ReferralProgramType,
    SignupAttribution,
)
from app.referrals import service as referrals


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
