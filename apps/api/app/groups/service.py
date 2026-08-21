"""Server-authoritative group lifecycle, contracts, and delegated authority."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.models.creator import CreatorProfile
from app.models.groups import (
    Group,
    GroupContract,
    GroupContractStatus,
    GroupCreatorMembership,
    GroupManagerMembership,
    GroupManagerRole,
    GroupMembershipStatus,
    GroupPermission,
    GroupPermissionGrant,
)
from app.models.identity import User


class GroupError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


async def manager_membership(
    db: AsyncSession, group_id: UUID, user_id: UUID
) -> GroupManagerMembership | None:
    return await db.scalar(
        select(GroupManagerMembership).where(
            GroupManagerMembership.group_id == group_id, GroupManagerMembership.user_id == user_id
        )
    )


async def require_group_manager(
    db: AsyncSession, group_id: UUID, user_id: UUID
) -> GroupManagerMembership:
    member = await manager_membership(db, group_id, user_id)
    if not member:
        raise PermissionError("Group management permission denied")
    return member


async def create_group(
    db: AsyncSession,
    actor: User,
    name: str,
    slug: str,
    default_creator_bps: int,
    description: str | None,
) -> Group:
    if not 0 <= default_creator_bps <= 10_000:
        raise GroupError("Default creator split must be between 0 and 10000 basis points")
    slug = slug.lower()
    if await db.scalar(select(Group).where(Group.slug == slug)):
        raise GroupError("Group slug already exists")
    group = Group(
        public_id=f"grp_{secrets.token_urlsafe(12)}",
        name=name.strip(),
        slug=slug,
        description=description,
        owner_user_id=actor.id,
        default_creator_basis_points=default_creator_bps,
    )
    db.add(group)
    await db.flush()
    db.add(GroupManagerMembership(group_id=group.id, user_id=actor.id, role=GroupManagerRole.owner))
    await record_event(
        db, "group.created", actor_user_id=actor.id, target_type="group", target_id=str(group.id)
    )
    return group


async def invite_creator(
    db: AsyncSession,
    group_id: UUID,
    actor: User,
    creator_id: UUID,
    creator_bps: int | None,
    permissions: list[GroupPermission],
) -> GroupCreatorMembership:
    await require_group_manager(db, group_id, actor.id)
    group = await db.get(Group, group_id)
    if not group or group.status.value != "active":
        raise GroupError("Group is not active")
    if not await db.get(CreatorProfile, creator_id):
        raise GroupError("Creator not found")
    existing = await db.scalar(
        select(GroupCreatorMembership).where(
            GroupCreatorMembership.creator_id == creator_id,
            GroupCreatorMembership.status.in_(
                [
                    GroupMembershipStatus.invited,
                    GroupMembershipStatus.pending_acceptance,
                    GroupMembershipStatus.active,
                    GroupMembershipStatus.leaving,
                ]
            ),
        )
    )
    if existing:
        raise GroupError("Creator already has a pending or active group membership")
    bps = group.default_creator_basis_points if creator_bps is None else creator_bps
    if not 0 <= bps <= 10_000:
        raise GroupError("Creator split must be between 0 and 10000 basis points")
    membership = GroupCreatorMembership(
        group_id=group.id, creator_id=creator_id, status=GroupMembershipStatus.invited
    )
    db.add(membership)
    await db.flush()
    contract = GroupContract(
        membership_id=membership.id,
        version=1,
        creator_basis_points=bps,
        group_basis_points=10_000 - bps,
        status=GroupContractStatus.proposed,
        proposed_by_user_id=actor.id,
    )
    db.add(contract)
    manager = await require_group_manager(db, group.id, actor.id)
    db.add_all(
        GroupPermissionGrant(
            membership_id=membership.id, manager_membership_id=manager.id, permission=p
        )
        for p in set(permissions)
    )
    await record_event(
        db,
        "group.invitation_created",
        actor_user_id=actor.id,
        target_type="group_membership",
        target_id=str(membership.id),
        metadata={"contract_id": str(contract.id)},
    )
    return membership


async def _creator_membership(
    db: AsyncSession, membership_id: UUID, actor: User
) -> GroupCreatorMembership:
    membership = await db.scalar(
        select(GroupCreatorMembership)
        .join(CreatorProfile)
        .where(GroupCreatorMembership.id == membership_id, CreatorProfile.user_id == actor.id)
        .with_for_update()
    )
    if not membership:
        raise PermissionError("Creator membership permission denied")
    return membership


async def accept_invitation(
    db: AsyncSession, membership_id: UUID, actor: User
) -> GroupCreatorMembership:
    membership = await _creator_membership(db, membership_id, actor)
    if membership.status is not GroupMembershipStatus.invited:
        raise GroupError("Invitation cannot be accepted")
    contract = await db.scalar(
        select(GroupContract)
        .where(GroupContract.membership_id == membership.id, GroupContract.version == 1)
        .with_for_update()
    )
    assert contract
    now = _now()
    membership.status, membership.joined_at = GroupMembershipStatus.active, now
    contract.status, contract.accepted_at, contract.effective_from = (
        GroupContractStatus.active,
        now,
        now,
    )
    await record_event(
        db,
        "group.invitation_accepted",
        actor_user_id=actor.id,
        target_type="group_membership",
        target_id=str(membership.id),
        metadata={"contract_id": str(contract.id)},
    )
    return membership


async def reject_invitation(
    db: AsyncSession, membership_id: UUID, actor: User
) -> GroupCreatorMembership:
    membership = await _creator_membership(db, membership_id, actor)
    if membership.status is not GroupMembershipStatus.invited:
        raise GroupError("Invitation cannot be rejected")
    contract = await db.scalar(
        select(GroupContract)
        .where(GroupContract.membership_id == membership.id, GroupContract.version == 1)
        .with_for_update()
    )
    assert contract
    membership.status, contract.status, contract.rejected_at = (
        GroupMembershipStatus.removed,
        GroupContractStatus.rejected,
        _now(),
    )
    await record_event(
        db,
        "group.invitation_rejected",
        actor_user_id=actor.id,
        target_type="group_membership",
        target_id=str(membership.id),
    )
    return membership


async def propose_amendment(
    db: AsyncSession, membership_id: UUID, actor: User, creator_bps: int
) -> GroupContract:
    membership = await db.get(GroupCreatorMembership, membership_id)
    if not membership or membership.status is not GroupMembershipStatus.active:
        raise GroupError("Membership is not active")
    await require_group_manager(db, membership.group_id, actor.id)
    if not 0 <= creator_bps <= 10_000:
        raise GroupError("Creator split must be between 0 and 10000 basis points")
    prior = await db.scalar(
        select(GroupContract).where(
            GroupContract.membership_id == membership.id,
            GroupContract.status == GroupContractStatus.proposed,
        )
    )
    if prior:
        raise GroupError("A contract proposal is already pending")
    version = (
        await db.scalar(
            select(func.max(GroupContract.version)).where(
                GroupContract.membership_id == membership.id
            )
        )
        or 0
    ) + 1
    active = await active_contract(db, membership.creator_id)
    contract = GroupContract(
        membership_id=membership.id,
        version=version,
        creator_basis_points=creator_bps,
        group_basis_points=10_000 - creator_bps,
        status=GroupContractStatus.proposed,
        proposed_by_user_id=actor.id,
        supersedes_contract_id=active.id if active else None,
    )
    db.add(contract)
    await db.flush()
    await record_event(
        db,
        "group.contract_proposed",
        actor_user_id=actor.id,
        target_type="group_contract",
        target_id=str(contract.id),
    )
    return contract


async def decide_amendment(
    db: AsyncSession, contract_id: UUID, actor: User, accept: bool
) -> GroupContract:
    contract = await db.scalar(
        select(GroupContract)
        .join(GroupCreatorMembership)
        .join(CreatorProfile)
        .where(GroupContract.id == contract_id, CreatorProfile.user_id == actor.id)
        .with_for_update()
    )
    if not contract:
        raise PermissionError("Contract permission denied")
    if contract.status is not GroupContractStatus.proposed or contract.version == 1:
        raise GroupError("Contract proposal cannot be decided")
    now = _now()
    if not accept:
        contract.status, contract.rejected_at = GroupContractStatus.rejected, now
    else:
        current = await db.get(GroupContract, contract.supersedes_contract_id)
        if not current or current.status is not GroupContractStatus.active:
            raise GroupError("Current contract is no longer active")
        current.status, current.effective_until = GroupContractStatus.ended, now
        contract.status, contract.accepted_at, contract.effective_from = (
            GroupContractStatus.active,
            now,
            now,
        )
    await record_event(
        db,
        "group.contract_accepted" if accept else "group.contract_rejected",
        actor_user_id=actor.id,
        target_type="group_contract",
        target_id=str(contract.id),
    )
    return contract


async def leave_membership(
    db: AsyncSession, membership_id: UUID, actor: User, removed_by_manager: bool = False
) -> GroupCreatorMembership:
    membership = await db.get(GroupCreatorMembership, membership_id)
    if not membership or membership.status is not GroupMembershipStatus.active:
        raise GroupError("Membership is not active")
    if removed_by_manager:
        await require_group_manager(db, membership.group_id, actor.id)
    else:
        await _creator_membership(db, membership_id, actor)
    now = _now()
    membership.status, membership.left_at = (
        (GroupMembershipStatus.removed if removed_by_manager else GroupMembershipStatus.left),
        now,
    )
    active = await db.scalar(
        select(GroupContract)
        .where(
            GroupContract.membership_id == membership.id,
            GroupContract.status == GroupContractStatus.active,
        )
        .with_for_update()
    )
    if active:
        active.status, active.effective_until = GroupContractStatus.ended, now
    # Grants are removed, so a future manager re-add requires an intentional new grant.
    grants = (
        await db.scalars(
            select(GroupPermissionGrant).where(GroupPermissionGrant.membership_id == membership.id)
        )
    ).all()
    for grant in grants:
        await db.delete(grant)
    await record_event(
        db,
        "group.creator_removed" if removed_by_manager else "group.creator_left",
        actor_user_id=actor.id,
        target_type="group_membership",
        target_id=str(membership.id),
    )
    return membership


async def set_affiliation_visibility(
    db: AsyncSession, membership_id: UUID, actor: User, visible: bool
) -> GroupCreatorMembership:
    membership = await _creator_membership(db, membership_id, actor)
    if membership.status is not GroupMembershipStatus.active:
        raise GroupError("Only active memberships can be public")
    membership.affiliation_public = visible
    await record_event(
        db,
        "group.affiliation_visibility_changed",
        actor_user_id=actor.id,
        target_type="group_membership",
        target_id=str(membership.id),
        metadata={"visible": visible},
    )
    return membership


async def active_contract(
    db: AsyncSession, creator_id: UUID, at: datetime | None = None
) -> GroupContract | None:
    """Return the contract in force at an event timestamp.

    Ended memberships and contracts remain queryable for historical financial
    reconciliation.  Their effective windows, rather than their current
    status, determine whether they governed an event.
    """
    at = at or _now()
    return await db.scalar(
        select(GroupContract)
        .join(GroupCreatorMembership)
        .where(
            GroupCreatorMembership.creator_id == creator_id,
            GroupCreatorMembership.joined_at <= at,
            (GroupCreatorMembership.left_at.is_(None)) | (GroupCreatorMembership.left_at > at),
            GroupContract.status.in_((GroupContractStatus.active, GroupContractStatus.ended)),
            GroupContract.effective_from <= at,
            (GroupContract.effective_until.is_(None)) | (GroupContract.effective_until > at),
        )
    )


async def has_delegated_permission(
    db: AsyncSession, actor_id: UUID, creator_id: UUID, permission: GroupPermission
) -> bool:
    return bool(
        await db.scalar(
            select(GroupPermissionGrant.id)
            .join(
                GroupCreatorMembership,
                GroupCreatorMembership.id == GroupPermissionGrant.membership_id,
            )
            .join(
                GroupManagerMembership,
                GroupManagerMembership.id == GroupPermissionGrant.manager_membership_id,
            )
            .where(
                GroupCreatorMembership.creator_id == creator_id,
                GroupCreatorMembership.status == GroupMembershipStatus.active,
                GroupManagerMembership.user_id == actor_id,
                GroupPermissionGrant.permission == permission,
            )
        )
    )


async def group_financial_dashboard(
    db: AsyncSession, group_id: UUID, actor: User, currency: str
) -> dict[str, int | str]:
    """Return group-owned ledger projections; never recompute old contract terms."""
    await require_group_manager(db, group_id, actor.id)
    from app.models.finance import LedgerAccount, LedgerDirection, LedgerEntry

    balances = await db.execute(
        select(
            LedgerAccount.kind,
            func.coalesce(
                func.sum(
                    case(
                        (LedgerEntry.direction == LedgerDirection.credit, LedgerEntry.amount_minor),
                        else_=-LedgerEntry.amount_minor,
                    )
                ),
                0,
            ),
        )
        .join(LedgerEntry, LedgerEntry.ledger_account_id == LedgerAccount.id)
        .where(LedgerAccount.owner_group_id == group_id, LedgerAccount.currency == currency.upper())
        .group_by(LedgerAccount.kind)
    )
    values = {kind.value: int(amount) for kind, amount in balances}
    active_creators = await db.scalar(
        select(func.count())
        .select_from(GroupCreatorMembership)
        .where(
            GroupCreatorMembership.group_id == group_id,
            GroupCreatorMembership.status == GroupMembershipStatus.active,
        )
    )
    return {
        "currency": currency.upper(),
        "active_creators": int(active_creators or 0),
        "pending_amount_minor": values.get("group_pending", 0),
        "available_amount_minor": values.get("group_available", 0),
    }
