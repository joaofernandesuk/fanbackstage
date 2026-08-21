from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentIdentity, Db
from app.audit.service import record_event
from app.content import service as content_service
from app.creators import service as creator_service
from app.finance import service as finance_service
from app.finance.service import currency_code
from app.groups import service
from app.messaging import service as messaging_service
from app.models.creator import CreatorProfile
from app.models.groups import (
    Group,
    GroupContract,
    GroupContractStatus,
    GroupCreatorMembership,
    GroupManagerMembership,
    GroupPermission,
)
from app.models.messaging import MessagingPermission
from app.permissions.policies import Permission, authorize
from app.schemas.content import ContentUpdate
from app.schemas.creator import CreatorProfileUpdate
from app.schemas.groups import (
    AffiliationVisibility,
    ContractAmendment,
    ContractResponse,
    GroupCreate,
    GroupResponse,
    InvitationCreate,
    ManagedCreatorResponse,
    MembershipResponse,
)
from app.schemas.messaging import MessagingSettingsInput
from app.schemas.streaming import CreatorLiveSettingsInput, CreatorLiveSettingsResponse
from app.schemas.subscription import PlanInput
from app.streaming import service as streaming_service
from app.subscriptions import service as subscription_service

router = APIRouter(prefix="/groups", tags=["groups"])


def group_response(row: Group) -> GroupResponse:
    return GroupResponse(
        id=row.id,
        name=row.name,
        slug=row.slug,
        default_creator_basis_points=row.default_creator_basis_points,
    )


def membership_response(row: GroupCreatorMembership) -> MembershipResponse:
    return MembershipResponse(
        id=row.id,
        group_id=row.group_id,
        creator_id=row.creator_id,
        status=row.status.value,
        affiliation_public=row.affiliation_public,
    )


def contract_response(row: GroupContract) -> ContractResponse:
    return ContractResponse(
        id=row.id,
        membership_id=row.membership_id,
        version=row.version,
        creator_basis_points=row.creator_basis_points,
        group_basis_points=row.group_basis_points,
        status=row.status.value,
    )


@router.post("", response_model=GroupResponse)
async def create(payload: GroupCreate, identity: CurrentIdentity, db: Db) -> GroupResponse:
    authorize(identity[0], Permission.MANAGER_ACCESS)
    try:
        group = await service.create_group(
            db,
            identity[0],
            name=payload.name,
            slug=payload.slug,
            default_creator_bps=payload.default_creator_basis_points,
            description=payload.description,
        )
        await db.commit()
        return group_response(group)
    except (service.GroupError, PermissionError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400 if isinstance(exc, service.GroupError) else 403, detail=str(exc)
        ) from exc


@router.post("/{group_id}/invitations", response_model=MembershipResponse)
async def invite(
    group_id: UUID, payload: InvitationCreate, identity: CurrentIdentity, db: Db
) -> MembershipResponse:
    try:
        permissions = [GroupPermission(item) for item in payload.permissions]
        membership = await service.invite_creator(
            db, group_id, identity[0], payload.creator_id, payload.creator_basis_points, permissions
        )
        await db.commit()
        return membership_response(membership)
    except (service.GroupError, PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400 if not isinstance(exc, PermissionError) else 403, detail=str(exc)
        ) from exc


@router.post("/memberships/{membership_id}/accept", response_model=MembershipResponse)
async def accept_invitation(
    membership_id: UUID, identity: CurrentIdentity, db: Db
) -> MembershipResponse:
    try:
        membership = await service.accept_invitation(db, membership_id, identity[0])
        await db.commit()
        return membership_response(membership)
    except (service.GroupError, PermissionError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400 if isinstance(exc, service.GroupError) else 403, detail=str(exc)
        ) from exc


@router.post("/memberships/{membership_id}/reject", response_model=MembershipResponse)
async def reject_invitation(
    membership_id: UUID, identity: CurrentIdentity, db: Db
) -> MembershipResponse:
    try:
        membership = await service.reject_invitation(db, membership_id, identity[0])
        await db.commit()
        return membership_response(membership)
    except (service.GroupError, PermissionError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400 if isinstance(exc, service.GroupError) else 403, detail=str(exc)
        ) from exc


@router.post("/memberships/{membership_id}/amendments", response_model=ContractResponse)
async def propose(
    membership_id: UUID, payload: ContractAmendment, identity: CurrentIdentity, db: Db
) -> ContractResponse:
    try:
        contract = await service.propose_amendment(
            db, membership_id, identity[0], payload.creator_basis_points
        )
        await db.commit()
        return contract_response(contract)
    except (service.GroupError, PermissionError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400 if isinstance(exc, service.GroupError) else 403, detail=str(exc)
        ) from exc


@router.post("/contracts/{contract_id}/accept", response_model=ContractResponse)
async def accept_contract(contract_id: UUID, identity: CurrentIdentity, db: Db) -> ContractResponse:
    try:
        contract = await service.decide_amendment(db, contract_id, identity[0], True)
        await db.commit()
        return contract_response(contract)
    except (service.GroupError, PermissionError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400 if isinstance(exc, service.GroupError) else 403, detail=str(exc)
        ) from exc


@router.post("/contracts/{contract_id}/reject", response_model=ContractResponse)
async def reject_contract(contract_id: UUID, identity: CurrentIdentity, db: Db) -> ContractResponse:
    try:
        contract = await service.decide_amendment(db, contract_id, identity[0], False)
        await db.commit()
        return contract_response(contract)
    except (service.GroupError, PermissionError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400 if isinstance(exc, service.GroupError) else 403, detail=str(exc)
        ) from exc


@router.post("/memberships/{membership_id}/leave", response_model=MembershipResponse)
async def leave(membership_id: UUID, identity: CurrentIdentity, db: Db) -> MembershipResponse:
    try:
        membership = await service.leave_membership(db, membership_id, identity[0])
        await db.commit()
        return membership_response(membership)
    except (service.GroupError, PermissionError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400 if isinstance(exc, service.GroupError) else 403, detail=str(exc)
        ) from exc


@router.delete("/memberships/{membership_id}", response_model=MembershipResponse)
async def remove(membership_id: UUID, identity: CurrentIdentity, db: Db) -> MembershipResponse:
    try:
        membership = await service.leave_membership(
            db, membership_id, identity[0], removed_by_manager=True
        )
        await db.commit()
        return membership_response(membership)
    except (service.GroupError, PermissionError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400 if isinstance(exc, service.GroupError) else 403, detail=str(exc)
        ) from exc


@router.patch("/memberships/{membership_id}/affiliation", response_model=MembershipResponse)
async def affiliation(
    membership_id: UUID, payload: AffiliationVisibility, identity: CurrentIdentity, db: Db
) -> MembershipResponse:
    try:
        membership = await service.set_affiliation_visibility(
            db, membership_id, identity[0], payload.visible
        )
        await db.commit()
        return membership_response(membership)
    except (service.GroupError, PermissionError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=400 if isinstance(exc, service.GroupError) else 403, detail=str(exc)
        ) from exc


@router.patch("/managed-content/{content_id}")
async def update_managed_content(
    content_id: UUID, payload: ContentUpdate, identity: CurrentIdentity, db: Db
) -> dict:
    try:
        content = await content_service.update_content_as_group_manager(
            db, identity[0], content_id, payload.model_dump(exclude_unset=True)
        )
        await db.commit()
        return {"id": str(content.id), "owner_creator_id": str(content.owner_creator_id)}
    except (PermissionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(
            status_code=403 if isinstance(exc, PermissionError) else 400, detail=str(exc)
        ) from exc


@router.patch("/managed-creators/{creator_id}/profile")
async def update_managed_creator_profile(
    creator_id: UUID, payload: CreatorProfileUpdate, identity: CurrentIdentity, db: Db
) -> dict:
    """Delegated profile edits retain the creator as owner and manager as actor."""
    creator = await db.get(CreatorProfile, creator_id)
    if not creator or not await service.has_delegated_permission(
        db, identity[0].id, creator_id, GroupPermission.edit_profile
    ):
        raise HTTPException(status_code=403, detail="Delegated profile permission denied")
    try:
        await creator_service.update_profile(
            db, creator, payload.model_dump(exclude_unset=True), identity[0].id
        )
        await record_event(
            db, "group_manager.profile_updated", actor_user_id=identity[0].id,
            target_type="creator_profile", target_id=str(creator.id),
        )
        await db.commit()
        return {"id": str(creator.id), "owner_creator_id": str(creator.id), "actor_user_id": str(identity[0].id)}
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/managed-creators/{creator_id}/live-settings", response_model=CreatorLiveSettingsResponse)
async def update_managed_creator_live_settings(
    creator_id: UUID, payload: CreatorLiveSettingsInput, identity: CurrentIdentity, db: Db
) -> CreatorLiveSettingsResponse:
    creator = await db.get(CreatorProfile, creator_id)
    if not creator or not await service.has_delegated_permission(
        db, identity[0].id, creator_id, GroupPermission.manage_live_settings
    ):
        raise HTTPException(status_code=403, detail="Delegated live-settings permission denied")
    settings = await streaming_service.settings_for_creator(db, creator.id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(settings, field, currency_code(value) if field == "currency" and value else value)
    if settings.max_authorization_minor < settings.one_to_one_price_minor * settings.minimum_minutes or settings.max_authorization_minor < settings.two_to_one_price_minor * settings.minimum_minutes:
        raise HTTPException(status_code=400, detail="Authorization cap must cover the 1-to-1 and 2-to-1 minimum charge")
    await record_event(
        db, "group_manager.live_settings_updated", actor_user_id=identity[0].id,
        target_type="creator_profile", target_id=str(creator.id),
    )
    await db.commit()
    return CreatorLiveSettingsResponse(
        private_sessions_enabled=settings.private_sessions_enabled,
        one_to_one_price_minor=settings.one_to_one_price_minor,
        two_to_one_price_minor=settings.two_to_one_price_minor,
        currency=settings.currency,
        minimum_minutes=settings.minimum_minutes,
        max_authorization_minor=settings.max_authorization_minor,
    )


@router.put("/managed-creators/{creator_id}/subscription-plan")
async def update_managed_subscription_plan(
    creator_id: UUID, payload: PlanInput, identity: CurrentIdentity, db: Db
) -> dict:
    if not await service.has_delegated_permission(
        db, identity[0].id, creator_id, GroupPermission.manage_subscriptions
    ):
        raise HTTPException(status_code=403, detail="Delegated subscription permission denied")
    try:
        plan = await subscription_service.configure_plan(
            db, creator_id, payload.currency, payload.enabled,
            [price.model_dump() for price in payload.prices],
        )
        await record_event(db, "group_manager.subscription_plan_updated", actor_user_id=identity[0].id, target_type="subscription_plan", target_id=str(plan.id))
        await db.commit()
        return {"id": str(plan.id), "creator_id": str(creator_id), "actor_user_id": str(identity[0].id)}
    except (subscription_service.SubscriptionError, ValueError) as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put("/managed-creators/{creator_id}/messaging-settings")
async def update_managed_messaging_settings(
    creator_id: UUID, payload: MessagingSettingsInput, identity: CurrentIdentity, db: Db
) -> dict:
    if not await service.has_delegated_permission(
        db, identity[0].id, creator_id, GroupPermission.manage_messaging
    ):
        raise HTTPException(status_code=403, detail="Delegated messaging permission denied")
    settings = await messaging_service.settings_for_creator(db, creator_id)
    settings.permission = MessagingPermission(payload.permission)
    settings.send_fee_minor = payload.send_fee_minor
    settings.send_fee_currency = payload.send_fee_currency.upper() if payload.send_fee_currency else None
    settings.subscribers_free = payload.subscribers_free
    if bool(settings.send_fee_minor) != bool(settings.send_fee_currency):
        raise HTTPException(status_code=400, detail="A send-fee currency is required with a send fee")
    await record_event(db, "group_manager.messaging_settings_updated", actor_user_id=identity[0].id, target_type="creator_profile", target_id=str(creator_id))
    await db.commit()
    return {"creator_id": str(creator_id), "actor_user_id": str(identity[0].id), "permission": settings.permission.value}


@router.get("/managed-creators/{creator_id}/earnings")
async def managed_creator_earnings(
    creator_id: UUID, identity: CurrentIdentity, db: Db, currency: str = "EUR"
) -> dict:
    if not await service.has_delegated_permission(
        db, identity[0].id, creator_id, GroupPermission.view_earnings
    ):
        raise HTTPException(status_code=403, detail="Delegated earnings permission denied")
    return {**(await finance_service.creator_financial_summary(db, creator_id, currency)), "currency": currency_code(currency), "creator_id": str(creator_id), "actor_user_id": str(identity[0].id)}


@router.get("/mine/memberships", response_model=list[MembershipResponse])
async def my_memberships(identity: CurrentIdentity, db: Db) -> list[MembershipResponse]:
    from sqlalchemy import select

    rows = (
        await db.scalars(
            select(GroupCreatorMembership)
            .join(CreatorProfile, CreatorProfile.id == GroupCreatorMembership.creator_id)
            .where(CreatorProfile.user_id == identity[0].id)
            .order_by(GroupCreatorMembership.created_at.desc())
        )
    ).all()
    result = []
    for row in rows:
        value = membership_response(row)
        contracts = (
            await db.scalars(
                select(GroupContract)
                .where(GroupContract.membership_id == row.id)
                .order_by(GroupContract.version.desc())
            )
        ).all()
        value.contracts = [contract_response(contract) for contract in contracts]
        result.append(value)
    return result


@router.get("/mine/managed", response_model=list[GroupResponse])
async def managed_groups(identity: CurrentIdentity, db: Db) -> list[GroupResponse]:
    from sqlalchemy import select

    rows = (
        await db.scalars(
            select(Group)
            .join(GroupManagerMembership, GroupManagerMembership.group_id == Group.id)
            .where(GroupManagerMembership.user_id == identity[0].id)
            .order_by(Group.name)
        )
    ).all()
    return [group_response(row) for row in rows]


@router.get("/{group_id}/managed-creators", response_model=list[ManagedCreatorResponse])
async def managed_creators(
    group_id: UUID, identity: CurrentIdentity, db: Db
) -> list[ManagedCreatorResponse]:
    await service.require_group_manager(db, group_id, identity[0].id)
    from sqlalchemy import select

    memberships = (
        await db.scalars(
            select(GroupCreatorMembership)
            .where(GroupCreatorMembership.group_id == group_id)
            .order_by(GroupCreatorMembership.created_at.desc())
        )
    ).all()
    result = []
    for membership in memberships:
        creator = await db.get(CreatorProfile, membership.creator_id)
        contract = await db.scalar(
            select(GroupContract)
            .where(
                GroupContract.membership_id == membership.id,
                GroupContract.status.in_(
                    [GroupContractStatus.active, GroupContractStatus.proposed]
                ),
            )
            .order_by(GroupContract.version.desc())
        )
        result.append(
            ManagedCreatorResponse(
                **membership_response(membership).model_dump(),
                username=creator.username if creator else None,
                display_name=creator.display_name if creator else None,
                active_contract=contract_response(contract) if contract else None,
            )
        )
    return result


@router.get("/{group_id}/dashboard")
async def dashboard(
    group_id: UUID, identity: CurrentIdentity, db: Db, currency: str = "EUR"
) -> dict:
    try:
        return await service.group_financial_dashboard(db, group_id, identity[0], currency)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/{group_id}", response_model=GroupResponse)
async def get_group(group_id: UUID, db: Db) -> GroupResponse:
    group = await db.get(Group, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    return group_response(group)
