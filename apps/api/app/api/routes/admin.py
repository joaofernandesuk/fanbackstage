from uuid import UUID

from fastapi import APIRouter, HTTPException
from sqlalchemy import or_, select

from app.api.deps import CurrentIdentity, Db
from app.audit.service import record_event
from app.content import service as content_service
from app.creators import service as creator_service
from app.marketplace import service as marketplace_service
from app.models.audit import AuditEvent
from app.models.content import ContentItem, ContentStatus, ModerationStatus
from app.models.creator import CreatorProfile, CreatorStatus
from app.models.groups import Group, GroupCreatorMembership
from app.models.marketplace import (
    MarketplaceEarningsHoldPolicy,
    MarketplaceListing,
    MarketplaceListingStatus,
    MarketplaceSellerRiskProfile,
    MarketplaceShippingAllowance,
)
from app.models.social import FeedPost, FeedPostStatus, PostComment, ReportStatus, SocialReport
from app.permissions.policies import Permission, authorize
from app.referrals import service as referral_service
from app.schemas.auth import MessageResponse
from app.schemas.marketplace import (
    MarketplaceHoldPolicyInput,
    MarketplaceHoldPolicyResponse,
    MarketplaceSellerSuspensionInput,
    MarketplaceSellerTierInput,
    MarketplaceSellerTierResponse,
    ShippingAllowanceInput,
    ShippingAllowanceResponse,
)
from app.schemas.referral import (
    AffiliatePartnerInput,
    AffiliatePartnerResponse,
    AffiliatePartnerStatusInput,
    ReferralLinkInput,
    ReferralLinkResponse,
    ReferralPolicyInput,
    ReferralProgramInput,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def affiliate_response(partner: referral_service.AffiliatePartner) -> AffiliatePartnerResponse:
    return AffiliatePartnerResponse(
        id=partner.id,
        public_id=partner.public_id,
        name=partner.name,
        status=partner.status.value,
    )


@router.post("/affiliates", response_model=AffiliatePartnerResponse)
async def create_affiliate(
    payload: AffiliatePartnerInput, identity: CurrentIdentity, db: Db
) -> AffiliatePartnerResponse:
    authorize(identity[0], Permission.FINANCIAL_CONFIGURE)
    try:
        partner = await referral_service.create_affiliate_partner(
            db, identity[0], name=payload.name, external_reference=payload.external_reference
        )
        await db.commit()
        return affiliate_response(partner)
    except referral_service.ReferralError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/affiliates", response_model=list[AffiliatePartnerResponse])
async def list_affiliates(identity: CurrentIdentity, db: Db) -> list[AffiliatePartnerResponse]:
    authorize(identity[0], Permission.FINANCIAL_CONFIGURE)
    rows = (
        await db.scalars(
            select(referral_service.AffiliatePartner).order_by(
                referral_service.AffiliatePartner.created_at
            )
        )
    ).all()
    return [affiliate_response(row) for row in rows]


@router.put("/affiliates/{partner_id}/status", response_model=AffiliatePartnerResponse)
async def change_affiliate_status(
    partner_id: UUID, payload: AffiliatePartnerStatusInput, identity: CurrentIdentity, db: Db
) -> AffiliatePartnerResponse:
    authorize(identity[0], Permission.FINANCIAL_CONFIGURE)
    partner = await db.get(referral_service.AffiliatePartner, partner_id)
    if not partner:
        raise HTTPException(status_code=404, detail="Affiliate partner not found")
    try:
        await referral_service.set_affiliate_partner_status(
            db, identity[0], partner, referral_service.AffiliatePartnerStatus(payload.status)
        )
        await db.commit()
        return affiliate_response(partner)
    except (ValueError, referral_service.ReferralError) as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/referrals/programs")
async def create_referral_program(
    payload: ReferralProgramInput, identity: CurrentIdentity, db: Db
) -> dict:
    authorize(identity[0], Permission.FINANCIAL_CONFIGURE)
    try:
        program = await referral_service.create_program(
            db,
            actor_type=referral_service.ReferralActorType(payload.actor_type),
            program_type=referral_service.ReferralProgramType(payload.program_type),
            owner_user_id=payload.owner_user_id,
            owner_creator_id=payload.owner_creator_id,
            affiliate_partner_id=payload.affiliate_partner_id,
            terms_reference=payload.terms_reference,
        )
        await db.commit()
        return {"id": program.id, "public_id": program.public_id, "status": program.status.value}
    except (ValueError, referral_service.ReferralError) as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/referrals/programs/{program_id}/policies")
async def create_referral_policy(
    program_id: UUID, payload: ReferralPolicyInput, identity: CurrentIdentity, db: Db
) -> dict:
    authorize(identity[0], Permission.FINANCIAL_CONFIGURE)
    program = await db.get(referral_service.ReferralProgram, program_id)
    if not program:
        raise HTTPException(status_code=404, detail="Referral program not found")
    try:
        policy = await referral_service.create_policy(
            db,
            program,
            basis_points=payload.basis_points,
            eligible_revenue_types=payload.eligible_revenue_types,
            attribution_window_days=payload.attribution_window_days,
            subscription_reward_window_days=payload.subscription_reward_window_days,
        )
        await db.commit()
        return {"id": policy.id, "public_id": policy.public_id, "version": policy.version}
    except referral_service.ReferralError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/referrals/programs/{program_id}/links", response_model=ReferralLinkResponse)
async def create_referral_link(
    program_id: UUID, payload: ReferralLinkInput, identity: CurrentIdentity, db: Db
) -> ReferralLinkResponse:
    authorize(identity[0], Permission.FINANCIAL_CONFIGURE)
    program = await db.get(referral_service.ReferralProgram, program_id)
    policy = await db.get(referral_service.ReferralCommissionPolicy, payload.policy_id)
    if not program or not policy or policy.program_id != program.id:
        raise HTTPException(status_code=404, detail="Referral program or policy not found")
    try:
        link = await referral_service.create_link(
            db,
            program,
            policy,
            code=payload.code,
            destination_path=payload.destination_path,
            source=payload.source,
            expires_at=payload.expires_at,
        )
        await db.commit()
        return ReferralLinkResponse(
            public_id=link.public_id,
            code=link.code,
            destination_path=link.destination_path,
            status=link.status.value,
            policy_id=link.policy_id,
        )
    except referral_service.ReferralError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def shipping_allowance_response(
    allowance: MarketplaceShippingAllowance,
) -> ShippingAllowanceResponse:
    return ShippingAllowanceResponse(**marketplace_service.allowance_snapshot(allowance))


def hold_policy_response(policy: MarketplaceEarningsHoldPolicy) -> MarketplaceHoldPolicyResponse:
    return MarketplaceHoldPolicyResponse(**marketplace_service.hold_policy_snapshot(policy))


def seller_tier_response(profile: MarketplaceSellerRiskProfile) -> MarketplaceSellerTierResponse:
    return MarketplaceSellerTierResponse(
        creator_id=profile.creator_id,
        tier=profile.tier.value,
        marketplace_suspended=profile.marketplace_suspended,
    )


@router.get("/marketplace/hold-policies", response_model=list[MarketplaceHoldPolicyResponse])
async def list_marketplace_hold_policies(
    identity: CurrentIdentity, db: Db
) -> list[MarketplaceHoldPolicyResponse]:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    rows = (
        await db.scalars(
            select(MarketplaceEarningsHoldPolicy).order_by(
                MarketplaceEarningsHoldPolicy.seller_tier
            )
        )
    ).all()
    return [hold_policy_response(row) for row in rows]


@router.put("/marketplace/hold-policies/{tier}", response_model=MarketplaceHoldPolicyResponse)
async def configure_marketplace_hold_policy(
    tier: str, payload: MarketplaceHoldPolicyInput, identity: CurrentIdentity, db: Db
) -> MarketplaceHoldPolicyResponse:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    try:
        policy = await marketplace_service.configure_hold_policy(
            db,
            identity[0],
            tier_value=tier,
            hold_duration_seconds=payload.hold_duration_seconds,
            active=payload.active,
            is_default=payload.is_default,
        )
        await db.commit()
        return hold_policy_response(policy)
    except marketplace_service.MarketplaceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/marketplace/sellers/{creator_id}/tier", response_model=MarketplaceSellerTierResponse)
async def get_marketplace_seller_tier(
    creator_id: UUID, identity: CurrentIdentity, db: Db
) -> MarketplaceSellerTierResponse:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    return seller_tier_response(await marketplace_service.seller_risk_profile(db, creator_id))


@router.put("/marketplace/sellers/{creator_id}/tier", response_model=MarketplaceSellerTierResponse)
async def change_marketplace_seller_tier(
    creator_id: UUID, payload: MarketplaceSellerTierInput, identity: CurrentIdentity, db: Db
) -> MarketplaceSellerTierResponse:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    try:
        profile = await marketplace_service.set_seller_tier(
            db, identity[0], creator_id, payload.tier, payload.reason
        )
        await db.commit()
        return seller_tier_response(profile)
    except marketplace_service.MarketplaceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.put(
    "/marketplace/sellers/{creator_id}/suspension", response_model=MarketplaceSellerTierResponse
)
async def change_marketplace_seller_suspension(
    creator_id: UUID, payload: MarketplaceSellerSuspensionInput, identity: CurrentIdentity, db: Db
) -> MarketplaceSellerTierResponse:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    try:
        profile = await marketplace_service.set_marketplace_suspension(
            db, identity[0], creator_id, payload.suspended, payload.reason
        )
        await db.commit()
        return seller_tier_response(profile)
    except marketplace_service.MarketplaceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/marketplace/audit")
async def marketplace_audit(identity: CurrentIdentity, db: Db) -> list[dict]:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    rows = (
        await db.scalars(
            select(AuditEvent)
            .where(AuditEvent.event_type.like("marketplace.%"))
            .order_by(AuditEvent.created_at.desc())
        )
    ).all()
    return [
        {
            "id": str(event.id),
            "event_type": event.event_type,
            "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "metadata": event.metadata_json,
            "created_at": event.created_at,
        }
        for event in rows
    ]


@router.post("/marketplace/earnings/release", response_model=dict)
async def release_eligible_marketplace_earnings(identity: CurrentIdentity, db: Db) -> dict:
    """Admin-operated replay-safe catch-up; scheduled workers use the same service."""
    authorize(identity[0], Permission.FINANCIAL_ACCESS)
    released = await marketplace_service.release_eligible_marketplace_earnings(db)
    await db.commit()
    return {"released": released}


@router.get("/marketplace/reports")
async def marketplace_reports(identity: CurrentIdentity, db: Db) -> list[dict]:
    authorize(identity[0], Permission.MODERATION_ACCESS)
    rows = (
        await db.scalars(
            select(SocialReport)
            .where(SocialReport.target_type == "marketplace_listing")
            .order_by(SocialReport.created_at.desc())
        )
    ).all()
    return [
        {
            "id": str(row.id),
            "listing_id": str(row.target_id),
            "reason": row.reason,
            "details": row.details,
            "status": row.status.value,
            "created_at": row.created_at,
        }
        for row in rows
    ]


@router.post("/marketplace/reports/{report_id}/remove-listing", response_model=MessageResponse)
async def remove_reported_marketplace_listing(
    report_id: UUID, identity: CurrentIdentity, db: Db
) -> MessageResponse:
    authorize(identity[0], Permission.MODERATION_ACCESS)
    report = await db.scalar(
        select(SocialReport).where(
            SocialReport.id == report_id, SocialReport.target_type == "marketplace_listing"
        )
    )
    if not report:
        raise HTTPException(status_code=404, detail="Marketplace report not found")
    listing = await db.get(MarketplaceListing, report.target_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Marketplace listing not found")
    listing.status = MarketplaceListingStatus.removed
    report.status = ReportStatus.reviewed
    await record_event(
        db,
        "marketplace.listing_removed_after_report",
        actor_user_id=identity[0].id,
        target_type="marketplace_listing",
        target_id=str(listing.id),
        metadata={"report_id": str(report.id), "reason": report.reason},
    )
    await db.commit()
    return MessageResponse(message="Marketplace listing removed")


@router.get("/marketplace/shipping-allowances", response_model=list[ShippingAllowanceResponse])
async def list_shipping_allowances(
    identity: CurrentIdentity,
    db: Db,
    country_code: str | None = None,
    region_code: str | None = None,
) -> list[ShippingAllowanceResponse]:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    query = select(MarketplaceShippingAllowance).order_by(
        MarketplaceShippingAllowance.currency,
        MarketplaceShippingAllowance.scope,
        MarketplaceShippingAllowance.destination_code,
    )
    if country_code:
        query = query.where(MarketplaceShippingAllowance.country_code == country_code.upper())
    if region_code:
        query = query.where(MarketplaceShippingAllowance.region_code == region_code.upper())
    rows = (await db.scalars(query)).all()
    return [shipping_allowance_response(row) for row in rows]


@router.put("/marketplace/shipping-allowances", response_model=ShippingAllowanceResponse)
async def configure_shipping_allowance(
    payload: ShippingAllowanceInput, identity: CurrentIdentity, db: Db
) -> ShippingAllowanceResponse:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    try:
        allowance = await marketplace_service.configure_shipping_allowance(
            db,
            identity[0],
            country_code=payload.country_code,
            region_code=payload.region_code,
            currency=payload.currency,
            allowed_shipping_minor=payload.allowed_shipping_minor,
            active=payload.active,
        )
        await db.commit()
        return shipping_allowance_response(allowance)
    except marketplace_service.MarketplaceError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete(
    "/marketplace/shipping-allowances/{allowance_id}", response_model=ShippingAllowanceResponse
)
async def disable_shipping_allowance(
    allowance_id: UUID, identity: CurrentIdentity, db: Db
) -> ShippingAllowanceResponse:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    try:
        allowance = await marketplace_service.disable_shipping_allowance(
            db, identity[0], allowance_id
        )
        await db.commit()
        return shipping_allowance_response(allowance)
    except marketplace_service.MarketplaceError as exc:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/groups")
async def groups_oversight(identity: CurrentIdentity, db: Db) -> list[dict]:
    """Platform-only inventory for group lifecycle and financial oversight."""
    authorize(identity[0], Permission.ADMIN_ACCESS)
    rows = (await db.scalars(select(Group).order_by(Group.created_at.desc()))).all()
    result = []
    for group in rows:
        memberships = await db.scalars(
            select(GroupCreatorMembership).where(GroupCreatorMembership.group_id == group.id)
        )
        memberships = list(memberships)
        result.append(
            {
                "id": str(group.id),
                "name": group.name,
                "slug": group.slug,
                "status": group.status.value,
                "creator_memberships": len(memberships),
                "active_creators": sum(item.status.value == "active" for item in memberships),
            }
        )
    return result


@router.get("/groups/{group_id}/audit")
async def group_audit(group_id: str, identity: CurrentIdentity, db: Db) -> list[dict]:
    """Platform-only audit trail scoped to a group and its memberships."""
    authorize(identity[0], Permission.ADMIN_ACCESS)
    group = await db.get(Group, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    membership_ids = [
        str(value)
        for value in (
            await db.scalars(
                select(GroupCreatorMembership.id).where(GroupCreatorMembership.group_id == group.id)
            )
        )
    ]
    rows = (
        await db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.event_type.like("group.%"),
                or_(
                    AuditEvent.target_id == str(group.id),
                    AuditEvent.target_id.in_(membership_ids) if membership_ids else False,
                ),
            )
            .order_by(AuditEvent.created_at.desc())
        )
    ).all()
    return [
        {
            "id": str(event.id),
            "event_type": event.event_type,
            "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
            "target_type": event.target_type,
            "target_id": event.target_id,
            "metadata": event.metadata_json,
            "created_at": event.created_at,
        }
        for event in rows
    ]


@router.get("/foundation", response_model=MessageResponse)
async def foundation(identity: CurrentIdentity) -> MessageResponse:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    return MessageResponse(message="FanBackstage admin foundation")


async def review_action(
    profile_id: str, target: CreatorStatus, identity: CurrentIdentity, db: Db
) -> MessageResponse:
    authorize(identity[0], Permission.ADMIN_ACCESS)
    profile = await db.scalar(select(CreatorProfile).where(CreatorProfile.id == profile_id))
    if not profile:
        raise HTTPException(status_code=404, detail="Creator application not found")
    try:
        await creator_service.set_status(db, profile, target, identity[0].id)
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MessageResponse(message=f"Creator {target.value}")


@router.get("/creator-applications", response_model=list[dict])
async def creator_applications(
    identity: CurrentIdentity, db: Db, status: CreatorStatus | None = None
):
    authorize(identity[0], Permission.ADMIN_ACCESS)
    query = select(CreatorProfile)
    if status:
        query = query.where(CreatorProfile.status == status)
    rows = (await db.scalars(query.order_by(CreatorProfile.created_at))).all()
    return [
        {
            "id": str(row.id),
            "username": row.username,
            "display_name": row.display_name,
            "status": row.status.value,
        }
        for row in rows
    ]


@router.post("/creator-applications/{profile_id}/approve", response_model=MessageResponse)
async def approve_creator(profile_id: str, identity: CurrentIdentity, db: Db) -> MessageResponse:
    return await review_action(profile_id, CreatorStatus.approved, identity, db)


@router.post("/creator-applications/{profile_id}/reject", response_model=MessageResponse)
async def reject_creator(profile_id: str, identity: CurrentIdentity, db: Db) -> MessageResponse:
    return await review_action(profile_id, CreatorStatus.rejected, identity, db)


@router.post("/creator-applications/{profile_id}/suspend", response_model=MessageResponse)
async def suspend_creator(profile_id: str, identity: CurrentIdentity, db: Db) -> MessageResponse:
    return await review_action(profile_id, CreatorStatus.suspended, identity, db)


async def content_review_action(
    content_id: str, action: str, identity: CurrentIdentity, db: Db
) -> MessageResponse:
    authorize(identity[0], Permission.MODERATION_ACCESS)
    content = await db.get(ContentItem, content_id)
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")
    try:
        if action == "approve":
            await content_service.approve(db, content, identity[0])
        else:
            await content_service.reject(db, content, identity[0])
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MessageResponse(message=f"Content {action}d")


@router.get("/content-review", response_model=list[dict])
async def content_review_queue(identity: CurrentIdentity, db: Db) -> list[dict]:
    authorize(identity[0], Permission.MODERATION_ACCESS)
    rows = (
        await db.scalars(
            select(ContentItem)
            .where(ContentItem.status == ContentStatus.pending_review)
            .order_by(ContentItem.created_at)
        )
    ).all()
    return [
        {"id": str(row.id), "title": row.title, "content_type": row.content_type.value}
        for row in rows
    ]


@router.post("/content-review/{content_id}/approve", response_model=MessageResponse)
async def approve_content(content_id: str, identity: CurrentIdentity, db: Db) -> MessageResponse:
    return await content_review_action(content_id, "approve", identity, db)


@router.post("/content-review/{content_id}/reject", response_model=MessageResponse)
async def reject_content(content_id: str, identity: CurrentIdentity, db: Db) -> MessageResponse:
    return await content_review_action(content_id, "reject", identity, db)


@router.get("/social-reports", response_model=list[dict])
async def social_reports(
    identity: CurrentIdentity, db: Db, status: ReportStatus | None = None, reason: str | None = None
) -> list[dict]:
    authorize(identity[0], Permission.MODERATION_ACCESS)
    query = select(SocialReport)
    if status:
        query = query.where(SocialReport.status == status)
    if reason:
        query = query.where(SocialReport.reason == reason)
    rows = (await db.scalars(query.order_by(SocialReport.created_at))).all()
    result = []
    for row in rows:
        target = await db.get(FeedPost if row.target_type == "post" else PostComment, row.target_id)
        result.append(
            {
                "id": str(row.id),
                "target_type": row.target_type,
                "target_id": str(row.target_id),
                "reason": row.reason,
                "details": row.details,
                "status": row.status.value,
                "created_at": row.created_at,
                "target_exists": target is not None,
                "target_preview": (target.body[:240] if target else None),
            }
        )
    return result


@router.post("/social-reports/{report_id}/dismiss", response_model=MessageResponse)
async def dismiss_social_report(
    report_id: str, identity: CurrentIdentity, db: Db
) -> MessageResponse:
    authorize(identity[0], Permission.MODERATION_ACCESS)
    report = await db.get(SocialReport, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    report.status = ReportStatus.dismissed
    await record_event(
        db,
        "social_report.dismissed",
        actor_user_id=identity[0].id,
        target_type="social_report",
        target_id=str(report.id),
    )
    await db.commit()
    return MessageResponse(message="Report dismissed")


@router.post("/social-reports/{report_id}/remove-target", response_model=MessageResponse)
async def remove_social_target(
    report_id: str, identity: CurrentIdentity, db: Db
) -> MessageResponse:
    authorize(identity[0], Permission.MODERATION_ACCESS)
    report = await db.get(SocialReport, report_id)
    if not report:
        raise HTTPException(404, "Report not found")
    target = await db.get(
        FeedPost if report.target_type == "post" else PostComment, report.target_id
    )
    if not target:
        raise HTTPException(404, "Report target not found")
    if report.target_type == "post":
        target.status = FeedPostStatus.removed
        target.moderation_status = ModerationStatus.removed
    else:
        from datetime import UTC, datetime

        target.hidden_at = datetime.now(UTC)
    report.status = ReportStatus.reviewed
    await record_event(
        db,
        "social_report.target_removed",
        actor_user_id=identity[0].id,
        target_type=report.target_type,
        target_id=str(report.target_id),
        metadata={"report_id": str(report.id)},
    )
    await db.commit()
    return MessageResponse(message="Reported target removed")
