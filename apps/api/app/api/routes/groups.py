from uuid import UUID

from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentIdentity, Db
from app.content import service as content_service
from app.groups import service
from app.models.groups import Group, GroupContract, GroupCreatorMembership, GroupPermission
from app.permissions.policies import Permission, authorize
from app.schemas.content import ContentUpdate
from app.schemas.groups import (
    AffiliationVisibility,
    ContractAmendment,
    ContractResponse,
    GroupCreate,
    GroupResponse,
    InvitationCreate,
    MembershipResponse,
)

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
        group = await service.create_group(db, identity[0], **payload.model_dump())
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
