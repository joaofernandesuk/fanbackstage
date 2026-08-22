from datetime import UTC, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import case, func, select

from app.accounts import service as accounts
from app.api.routes import admin as admin_routes
from app.api.routes import groups as group_routes
from app.content import service as content_service
from app.content.access import can_access_content
from app.creators import service as creators
from app.finance import service as finance
from app.groups import service as groups
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
    PaymentAttempt,
    PaymentStatus,
)
from app.models.groups import (
    GroupContract,
    GroupContractStatus,
    GroupPermission,
    GroupPermissionGrant,
)
from app.schemas.creator import CreatorProfileUpdate
from app.schemas.streaming import CreatorLiveSettingsInput


async def approved_creator(db, email):
    user, _ = await accounts.register(db, email, "strong-password-123", None)
    profile = await creators.get_or_create_profile(db, user)
    await creators.update_profile(
        db, profile, {"username": email.split("@")[0], "display_name": "Creator"}, user.id
    )
    await creators.submit(db, profile, user.id)
    await creators.development_verify(db, profile, True, user.id)
    await creators.set_status(db, profile, CreatorStatus.approved, user.id)
    return user, profile


@pytest.mark.asyncio
async def test_contract_acceptance_snapshots_allocation_and_exit_revokes_delegation(db_session):
    manager, _ = await accounts.register(
        db_session, "manager@example.com", "strong-password-123", None
    )
    creator_user, creator = await approved_creator(db_session, "group-creator@example.com")
    buyer, _ = await accounts.register(
        db_session, "group-buyer@example.com", "strong-password-123", None
    )
    group = await groups.create_group(db_session, manager, "A Group", "a-group", 5_000, None)
    membership = await groups.invite_creator(
        db_session, group.id, manager, creator.id, None, [GroupPermission.manage_content]
    )
    assert membership.status.value == "invited"
    await groups.accept_invitation(db_session, membership.id, creator_user)
    assert await groups.has_delegated_permission(
        db_session, manager.id, creator.id, GroupPermission.manage_content
    )

    content = ContentItem(
        owner_creator_id=creator.id,
        created_by_user_id=creator_user.id,
        content_type=ContentType.gallery,
        title="Split PPV",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=2000,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    purchase = await finance.initiate_purchase(db_session, buyer, content.id, "split-ppv")
    attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    payload, signature = finance.development_webhook_payload(attempt)
    settled = await finance.process_development_webhook(db_session, payload, signature)
    assert settled and await can_access_content(db_session, content, buyer)
    entries = (
        await db_session.execute(
            select(LedgerEntry, LedgerAccount.kind)
            .join(LedgerAccount)
            .where(LedgerEntry.transaction_id == settled.ledger_transaction_id)
        )
    ).all()
    credits = {
        kind: amount
        for entry, kind in entries
        if entry.direction is LedgerDirection.credit
        for amount in [entry.amount_minor]
    }
    assert credits[LedgerAccountKind.platform_revenue] == 400
    assert credits[LedgerAccountKind.creator_pending] == 800
    assert credits[LedgerAccountKind.group_pending] == 800
    dashboard_after_ppv = await groups.group_financial_dashboard(
        db_session, group.id, manager, "EUR"
    )
    assert dashboard_after_ppv["pending_amount_minor"] == 800
    assert dashboard_after_ppv["source_amounts_minor"]["ppv"] == 800

    refunded = await finance.refund_purchase(db_session, settled, manager, "Support refund")
    assert refunded.status.value == "refunded"
    group_pending = await db_session.scalar(
        select(LedgerAccount).where(
            LedgerAccount.kind == LedgerAccountKind.group_pending,
            LedgerAccount.owner_group_id == group.id,
        )
    )
    assert group_pending
    group_balance = await db_session.scalar(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (LedgerEntry.direction == LedgerDirection.credit, LedgerEntry.amount_minor),
                        else_=-LedgerEntry.amount_minor,
                    )
                ),
                0,
            )
        ).where(LedgerEntry.ledger_account_id == group_pending.id)
    )
    assert group_balance == 0
    dashboard = await groups.group_financial_dashboard(db_session, group.id, manager, "EUR")
    assert dashboard == {
        "currency": "EUR",
        "active_creators": 1,
        "pending_amount_minor": 0,
        "available_amount_minor": 0,
        "source_amounts_minor": {
            "ppv": 0,
            "subscriptions": 0,
            "messaging": 0,
            "private_live": 0,
            "marketplace": 0,
        },
    }
    membership.affiliation_public = True
    public_rows = await group_routes.public_affiliations(group.id, db_session)
    assert public_rows == [
        {
            "creator_id": str(creator.id),
            "username": creator.username,
            "display_name": creator.display_name,
        }
    ]
    admin, _ = await accounts.register(
        db_session, "group-oversight@example.com", "strong-password-123", None
    )
    await accounts.assign_role(db_session, admin, "admin", admin.id, None)
    overview = await admin_routes.groups_oversight((admin, None), db_session)
    assert next(row for row in overview if row["id"] == str(group.id))["active_creators"] == 1
    audit_rows = await admin_routes.group_audit(str(group.id), (admin, None), db_session)
    assert any(row["event_type"] == "group.invitation_accepted" for row in audit_rows)

    # Defaults are future-only: Creator A's accepted 50/50 contract does not
    # change when the group offers 30/70 to a later creator.
    group.default_creator_basis_points = 3_000
    creator_b_user, creator_b = await approved_creator(db_session, "group-creator-b@example.com")
    membership_b = await groups.invite_creator(
        db_session, group.id, manager, creator_b.id, None, []
    )
    await groups.accept_invitation(db_session, membership_b.id, creator_b_user)
    a_before = await groups.active_contract(db_session, creator.id)
    b_contract = await groups.active_contract(db_session, creator_b.id)
    assert a_before and a_before.creator_basis_points == 5_000
    assert b_contract and b_contract.creator_basis_points == 3_000
    b_content = ContentItem(
        owner_creator_id=creator_b.id,
        created_by_user_id=creator_b_user.id,
        content_type=ContentType.gallery,
        title="B split PPV",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=2000,
        price_currency="EUR",
    )
    db_session.add(b_content)
    await db_session.flush()
    buyer_b, _ = await accounts.register(
        db_session, "group-buyer-b@example.com", "strong-password-123", None
    )
    b_purchase = await finance.initiate_purchase(db_session, buyer_b, b_content.id, "b-split-ppv")
    b_attempt = await db_session.get(PaymentAttempt, b_purchase.payment_attempt_id)
    b_payload, b_signature = finance.development_webhook_payload(b_attempt)
    assert await finance.process_development_webhook(db_session, b_payload, b_signature)
    dashboard_before_default_change = await groups.group_financial_dashboard(
        db_session, group.id, manager, "EUR"
    )
    assert dashboard_before_default_change["pending_amount_minor"] == 1_120
    group.default_creator_basis_points = 1_000
    assert (
        await groups.group_financial_dashboard(db_session, group.id, manager, "EUR")
        == dashboard_before_default_change
    )

    amendment = await groups.propose_amendment(db_session, membership.id, manager, 7_000)
    assert amendment.status is GroupContractStatus.proposed
    await groups.decide_amendment(db_session, amendment.id, creator_user, True)
    active = await groups.active_contract(db_session, creator.id)
    assert active and active.creator_basis_points == 7_000
    old_contract = await db_session.get(GroupContract, a_before.id)
    assert old_contract and old_contract.status is GroupContractStatus.ended
    historical_buyer, _ = await accounts.register(
        db_session, "historical-exit-buyer@example.com", "strong-password-123", None
    )
    historical_purchase = await finance.initiate_purchase(
        db_session, historical_buyer, content.id, "historical-before-exit"
    )
    historical_attempt = await db_session.get(
        PaymentAttempt, historical_purchase.payment_attempt_id
    )
    historical_payload, historical_signature = finance.development_webhook_payload(
        historical_attempt
    )
    historical_settled = await finance.process_development_webhook(
        db_session, historical_payload, historical_signature
    )
    assert historical_settled and historical_settled.ledger_transaction_id
    historical_ledger = await db_session.get(
        LedgerTransaction, historical_settled.ledger_transaction_id
    )
    assert historical_ledger and historical_ledger.metadata_json["group_amount_minor"] == "480"
    assert historical_ledger.metadata_json["group_contract_id"] == str(active.id)
    delayed_buyer, _ = await accounts.register(
        db_session, "delayed-exit-buyer@example.com", "strong-password-123", None
    )
    delayed_purchase = await finance.initiate_purchase(
        db_session, delayed_buyer, content.id, "delayed-settlement-before-exit"
    )
    delayed_attempt = await db_session.get(PaymentAttempt, delayed_purchase.payment_attempt_id)
    assert delayed_attempt
    # This is a provider-confirmed financial event whose durable settlement is
    # delayed until after the creator leaves the group.
    delayed_attempt.status = PaymentStatus.succeeded
    delayed_attempt.completed_at = datetime.now(UTC)
    await db_session.flush()
    await groups.leave_membership(db_session, membership.id, creator_user)
    assert not await groups.has_delegated_permission(
        db_session, manager.id, creator.id, GroupPermission.manage_content
    )
    assert (await db_session.get(GroupContract, active.id)).status is GroupContractStatus.ended
    assert await finance.reconcile_succeeded_payments(db_session) == 1
    assert delayed_purchase.ledger_transaction_id
    delayed_ledger = await db_session.get(LedgerTransaction, delayed_purchase.ledger_transaction_id)
    assert delayed_ledger and delayed_ledger.metadata_json["group_amount_minor"] == "480"
    assert delayed_ledger.metadata_json["group_contract_id"] == str(active.id)
    historical_refund = await finance.refund_purchase(
        db_session, historical_settled, manager, "Refund after creator exit"
    )
    refund_ledger = await db_session.scalar(
        select(LedgerTransaction).where(
            LedgerTransaction.reference == f"refund:{historical_refund.id}"
        )
    )
    assert refund_ledger and refund_ledger.metadata_json["group_amount_minor"] == "480"
    assert refund_ledger.metadata_json["original_group_contract_id"] == str(active.id)
    # Leave is a future-only boundary: the group keeps its immutable historical
    # earnings, while a later paid event credits the creator's full post-fee pool.
    group_before_exit_sale = await groups.group_financial_dashboard(
        db_session, group.id, manager, "EUR"
    )
    post_exit_buyer, _ = await accounts.register(
        db_session, "post-exit-buyer@example.com", "strong-password-123", None
    )
    post_exit_purchase = await finance.initiate_purchase(
        db_session, post_exit_buyer, content.id, "post-exit-creator-revenue"
    )
    post_exit_attempt = await db_session.get(PaymentAttempt, post_exit_purchase.payment_attempt_id)
    post_exit_payload, post_exit_signature = finance.development_webhook_payload(post_exit_attempt)
    post_exit_settled = await finance.process_development_webhook(
        db_session, post_exit_payload, post_exit_signature
    )
    assert post_exit_settled and post_exit_settled.ledger_transaction_id
    post_exit_ledger = await db_session.get(
        LedgerTransaction, post_exit_settled.ledger_transaction_id
    )
    assert post_exit_ledger and post_exit_ledger.metadata_json["group_amount_minor"] == "0"
    assert post_exit_ledger.metadata_json["creator_amount_minor"] == "1600"
    assert (
        await groups.group_financial_dashboard(db_session, group.id, manager, "EUR")
        == group_before_exit_sale
    )


@pytest.mark.asyncio
async def test_contract_amendments_change_only_future_financial_allocations(db_session):
    manager, _ = await accounts.register(
        db_session, "amendment-manager@example.com", "strong-password-123", None
    )
    creator_user, creator = await approved_creator(db_session, "amendment-creator@example.com")
    group = await groups.create_group(db_session, manager, "Amendments", "amendments", 5_000, None)
    membership = await groups.invite_creator(db_session, group.id, manager, creator.id, None, [])
    await groups.accept_invitation(db_session, membership.id, creator_user)
    content = ContentItem(
        owner_creator_id=creator.id,
        created_by_user_id=creator_user.id,
        content_type=ContentType.gallery,
        title="Immutable amendment allocation",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=2000,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()

    async def settle(reference: str) -> LedgerTransaction:
        buyer, _ = await accounts.register(
            db_session, f"{reference}@example.com", "strong-password-123", None
        )
        purchase = await finance.initiate_purchase(db_session, buyer, content.id, reference)
        attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
        payload, signature = finance.development_webhook_payload(attempt)
        settled = await finance.process_development_webhook(db_session, payload, signature)
        assert settled and settled.ledger_transaction_id
        ledger = await db_session.get(LedgerTransaction, settled.ledger_transaction_id)
        assert ledger
        return ledger

    before = await settle("before-amendment")
    assert before.metadata_json["creator_amount_minor"] == "800"
    assert before.metadata_json["group_amount_minor"] == "800"
    assert before.metadata_json["group_contract_version"] == "1"

    rejected = await groups.propose_amendment(db_session, membership.id, manager, 7_000)
    pending = await settle("while-pending")
    assert pending.metadata_json["creator_amount_minor"] == "800"
    assert pending.metadata_json["group_contract_version"] == "1"
    await groups.decide_amendment(db_session, rejected.id, creator_user, False)
    after_rejection = await settle("after-rejection")
    assert after_rejection.metadata_json["creator_amount_minor"] == "800"
    assert after_rejection.metadata_json["group_amount_minor"] == "800"

    accepted = await groups.propose_amendment(db_session, membership.id, manager, 7_000)
    await groups.decide_amendment(db_session, accepted.id, creator_user, True)
    after_acceptance = await settle("after-acceptance")
    assert after_acceptance.metadata_json["creator_amount_minor"] == "1120"
    assert after_acceptance.metadata_json["group_amount_minor"] == "480"
    assert after_acceptance.metadata_json["group_contract_version"] == "3"
    # The ledger stores the accepted event-time split; neither a rejection nor
    # the later acceptance can mutate those earlier financial records.
    assert (
        await db_session.get(LedgerTransaction, before.id)
    ).metadata_json == before.metadata_json
    assert (
        await db_session.get(LedgerTransaction, pending.id)
    ).metadata_json == pending.metadata_json


@pytest.mark.asyncio
async def test_delegated_profile_and_live_settings_are_scoped_audited_and_revoked(db_session):
    manager, _ = await accounts.register(
        db_session, "operations-manager@example.com", "strong-password-123", None
    )
    creator_user, creator = await approved_creator(db_session, "operations-creator@example.com")
    _, unrelated_creator = await approved_creator(db_session, "operations-unrelated@example.com")
    group = await groups.create_group(db_session, manager, "Operations", "operations", 5_000, None)
    membership = await groups.invite_creator(db_session, group.id, manager, creator.id, None, [])
    await groups.accept_invitation(db_session, membership.id, creator_user)
    manager_membership = await groups.manager_membership(db_session, group.id, manager.id)
    assert manager_membership
    db_session.add_all(
        [
            GroupPermissionGrant(
                membership_id=membership.id,
                manager_membership_id=manager_membership.id,
                permission=GroupPermission.edit_profile,
            ),
            GroupPermissionGrant(
                membership_id=membership.id,
                manager_membership_id=manager_membership.id,
                permission=GroupPermission.manage_live_settings,
            ),
            GroupPermissionGrant(
                membership_id=membership.id,
                manager_membership_id=manager_membership.id,
                permission=GroupPermission.view_earnings,
            ),
            GroupPermissionGrant(
                membership_id=membership.id,
                manager_membership_id=manager_membership.id,
                permission=GroupPermission.view_analytics,
            ),
        ]
    )
    await db_session.flush()
    identity = (manager, None)
    result = await group_routes.update_managed_creator_profile(
        creator.id,
        CreatorProfileUpdate(display_name="Manager-set display name"),
        identity,
        db_session,
    )
    assert result["owner_creator_id"] == str(creator.id)
    assert result["actor_user_id"] == str(manager.id)
    settings = await group_routes.update_managed_creator_live_settings(
        creator.id, CreatorLiveSettingsInput(one_to_one_price_minor=777), identity, db_session
    )
    assert settings.one_to_one_price_minor == 777
    assert (await group_routes.managed_creator_earnings(creator.id, identity, db_session))[
        "creator_id"
    ] == str(creator.id)
    with pytest.raises(HTTPException, match="Delegated earnings permission denied"):
        await group_routes.managed_creator_earnings(unrelated_creator.id, identity, db_session)
    assert (await group_routes.managed_creator_analytics(creator.id, identity, db_session))[
        "creator_id"
    ] == str(creator.id)
    with pytest.raises(HTTPException, match="Delegated analytics permission denied"):
        await group_routes.managed_creator_analytics(unrelated_creator.id, identity, db_session)
    with pytest.raises(HTTPException, match="Delegated profile permission denied"):
        await group_routes.update_managed_creator_profile(
            unrelated_creator.id,
            CreatorProfileUpdate(display_name="Cross-creator attempt"),
            identity,
            db_session,
        )
    events = set(
        (
            await db_session.scalars(
                select(AuditEvent.event_type).where(AuditEvent.actor_user_id == manager.id)
            )
        ).all()
    )
    assert {"group_manager.profile_updated", "group_manager.live_settings_updated"} <= events
    await groups.leave_membership(db_session, membership.id, creator_user)
    with pytest.raises(HTTPException, match="Delegated profile permission denied"):
        await group_routes.update_managed_creator_profile(
            creator.id, CreatorProfileUpdate(display_name="Stale manager"), identity, db_session
        )


@pytest.mark.asyncio
async def test_delegated_content_control_is_scoped_audited_and_revoked_immediately(db_session):
    manager, _ = await accounts.register(
        db_session, "scoped-manager@example.com", "strong-password-123", None
    )
    creator_user, creator = await approved_creator(db_session, "scoped-creator@example.com")
    _, unrelated_creator = await approved_creator(db_session, "unrelated-creator@example.com")
    group = await groups.create_group(db_session, manager, "Scoped", "scoped", 5_000, None)
    membership = await groups.invite_creator(db_session, group.id, manager, creator.id, None, [])
    await groups.accept_invitation(db_session, membership.id, creator_user)
    content = ContentItem(
        owner_creator_id=creator.id,
        created_by_user_id=creator_user.id,
        content_type=ContentType.gallery,
        title="Owner content",
        access_policy=AccessPolicy.free,
    )
    other = ContentItem(
        owner_creator_id=unrelated_creator.id,
        created_by_user_id=unrelated_creator.user_id,
        content_type=ContentType.gallery,
        title="Other content",
        access_policy=AccessPolicy.free,
    )
    db_session.add_all([content, other])
    await db_session.flush()
    with pytest.raises(PermissionError):
        await content_service.update_content_as_group_manager(
            db_session, manager, content.id, {"title": "Denied"}
        )
    with pytest.raises(PermissionError):
        await content_service.update_content_as_group_manager(
            db_session, manager, other.id, {"title": "Denied"}
        )
    manager_membership = await groups.manager_membership(db_session, group.id, manager.id)
    from app.models.groups import GroupPermissionGrant

    db_session.add(
        GroupPermissionGrant(
            membership_id=membership.id,
            manager_membership_id=manager_membership.id,
            permission=GroupPermission.manage_content,
        )
    )
    await db_session.flush()
    updated = await content_service.update_content_as_group_manager(
        db_session, manager, content.id, {"title": "Manager update"}
    )
    assert updated.owner_creator_id == creator.id and updated.created_by_user_id == creator_user.id
    audit = await db_session.scalar(
        select(AuditEvent).where(AuditEvent.event_type == "group_manager.content_updated")
    )
    assert audit and audit.actor_user_id == manager.id
    await groups.leave_membership(db_session, membership.id, creator_user)
    with pytest.raises(PermissionError):
        await content_service.update_content_as_group_manager(
            db_session, manager, content.id, {"title": "Stale session blocked"}
        )
