import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.core.config import get_settings
from app.models.creator import CreatorProfile
from app.models.identity import User
from app.models.referral import (
    AffiliatePartner,
    AffiliatePartnerStatus,
    ReferralActorType,
    ReferralCommissionAllocation,
    ReferralCommissionPolicy,
    ReferralLink,
    ReferralLinkStatus,
    ReferralPolicyStatus,
    ReferralProgram,
    ReferralProgramStatus,
    ReferralProgramType,
    ReferralSubscriptionRewardWindow,
    ReferralTouch,
    SignupAttribution,
)

ATTRIBUTION_COOKIE_NAME = "fanbackstage_referral"
_TOUCH_DEDUPE_SECONDS = 15 * 60


class ReferralError(ValueError):
    pass


def now() -> datetime:
    return datetime.now(UTC)


def opaque_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"


def normalize_code(code: str) -> str:
    normalized = code.strip().upper()
    if (
        not normalized
        or len(normalized) > 64
        or not all(char.isalnum() or char in "-_" for char in normalized)
    ):
        raise ReferralError("Referral code is invalid")
    return normalized


def safe_destination(destination_path: str) -> str:
    parsed = urlsplit(destination_path)
    if (
        parsed.scheme
        or parsed.netloc
        or not parsed.path.startswith("/")
        or parsed.path.startswith("//")
    ):
        raise ReferralError("Referral destination must be an internal path")
    if "\\" in destination_path or len(destination_path) > 512:
        raise ReferralError("Referral destination is invalid")
    return destination_path


def policy_snapshot(policy: ReferralCommissionPolicy) -> dict[str, object]:
    return {
        "policy_id": str(policy.id),
        "version": policy.version,
        "basis_points": policy.basis_points,
        "attribution_window_days": policy.attribution_window_days,
        "subscription_reward_window_days": policy.subscription_reward_window_days,
        "eligible_revenue_types": list(policy.eligible_revenue_types),
        "commission_funding": "platform_commission",
        "attribution_policy": "last_eligible_touch",
    }


def _sign(payload: dict[str, object]) -> str:
    encoded = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(
        b"="
    )
    signature = hmac.new(get_settings().session_secret.encode(), encoded, hashlib.sha256).digest()
    return f"{encoded.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def _verify(token: str) -> dict[str, object] | None:
    try:
        encoded, supplied = token.split(".", 1)
        expected = hmac.new(
            get_settings().session_secret.encode(), encoded.encode(), hashlib.sha256
        ).digest()
        actual = base64.urlsafe_b64decode(supplied + "=" * (-len(supplied) % 4))
        if not hmac.compare_digest(expected, actual):
            return None
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        return payload if int(payload["expires_at"]) > int(now().timestamp()) else None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


async def create_program(
    db: AsyncSession,
    *,
    actor_type: ReferralActorType,
    program_type: ReferralProgramType,
    owner_user_id: UUID | None = None,
    owner_creator_id: UUID | None = None,
    affiliate_partner_id: UUID | None = None,
    terms_reference: str | None = None,
) -> ReferralProgram:
    required_owner = {
        ReferralActorType.creator: owner_creator_id,
        ReferralActorType.user: owner_user_id,
        ReferralActorType.affiliate_partner: affiliate_partner_id,
    }.get(actor_type)
    if (
        program_type is not ReferralProgramType.creator_creator_referral
        and actor_type is not ReferralActorType.platform_campaign
        and not required_owner
    ):
        raise ReferralError("Referral program beneficiary is required")
    if program_type is ReferralProgramType.creator_creator_referral:
        status = ReferralProgramStatus.paused
    else:
        status = ReferralProgramStatus.active
    program = ReferralProgram(
        public_id=opaque_id("rp"),
        actor_type=actor_type,
        owner_user_id=owner_user_id,
        owner_creator_id=owner_creator_id,
        affiliate_partner_id=affiliate_partner_id,
        program_type=program_type,
        status=status,
        terms_reference=terms_reference,
    )
    db.add(program)
    await db.flush()
    return program


async def create_policy(
    db: AsyncSession,
    program: ReferralProgram,
    *,
    basis_points: int,
    eligible_revenue_types: list[str],
    attribution_window_days: int = 30,
    subscription_reward_window_days: int = 90,
) -> ReferralCommissionPolicy:
    if not 0 <= basis_points <= 10_000:
        raise ReferralError("Referral commission basis points are invalid")
    if attribution_window_days <= 0 or subscription_reward_window_days <= 0:
        raise ReferralError("Referral policy window is invalid")
    version = (
        int(
            (
                await db.scalar(
                    select(ReferralCommissionPolicy.version)
                    .where(ReferralCommissionPolicy.program_id == program.id)
                    .order_by(ReferralCommissionPolicy.version.desc())
                    .limit(1)
                )
            )
            or 0
        )
        + 1
    )
    policy = ReferralCommissionPolicy(
        public_id=opaque_id("rcp"),
        program_id=program.id,
        version=version,
        basis_points=basis_points,
        attribution_window_days=attribution_window_days,
        subscription_reward_window_days=subscription_reward_window_days,
        eligible_revenue_types=sorted(set(eligible_revenue_types)),
        status=ReferralPolicyStatus.active,
        effective_from=now(),
    )
    db.add(policy)
    await db.flush()
    return policy


async def create_link(
    db: AsyncSession,
    program: ReferralProgram,
    policy: ReferralCommissionPolicy,
    *,
    code: str,
    destination_path: str,
    source: str | None = None,
    expires_at: datetime | None = None,
) -> ReferralLink:
    if program.status is not ReferralProgramStatus.active:
        raise ReferralError("Referral program is not active")
    link = ReferralLink(
        public_id=opaque_id("rl"),
        program_id=program.id,
        policy_id=policy.id,
        code=normalize_code(code),
        destination_path=safe_destination(destination_path),
        status=ReferralLinkStatus.active,
        source=source[:80] if source else None,
        expires_at=expires_at,
    )
    db.add(link)
    await db.flush()
    return link


async def create_affiliate_partner(
    db: AsyncSession,
    actor: User,
    *,
    name: str,
    external_reference: str | None = None,
    owner_user_id: UUID | None = None,
) -> AffiliatePartner:
    partner = AffiliatePartner(
        public_id=opaque_id("ap"),
        name=name.strip(),
        status=AffiliatePartnerStatus.active,
        external_reference=external_reference,
        owner_user_id=owner_user_id,
    )
    if not partner.name:
        raise ReferralError("Affiliate partner name is required")
    db.add(partner)
    await db.flush()
    await record_event(
        db,
        "referral.affiliate_partner_created",
        actor_user_id=actor.id,
        target_type="affiliate_partner",
        target_id=str(partner.id),
        metadata={
            "public_id": partner.public_id,
            "owner_user_id": str(owner_user_id) if owner_user_id else None,
        },
    )
    return partner


async def set_affiliate_partner_status(
    db: AsyncSession,
    actor: User,
    partner: AffiliatePartner,
    status: AffiliatePartnerStatus,
) -> AffiliatePartner:
    if partner.status is AffiliatePartnerStatus.terminated:
        raise ReferralError("A terminated affiliate partner cannot be reactivated")
    old_status = partner.status
    partner.status = status
    partner.suspended_at = now() if status is AffiliatePartnerStatus.suspended else None
    await record_event(
        db,
        "referral.affiliate_partner_status_changed",
        actor_user_id=actor.id,
        target_type="affiliate_partner",
        target_id=str(partner.id),
        metadata={"old_status": old_status.value, "new_status": status.value},
    )
    return partner


async def resolve_click(
    db: AsyncSession,
    code: str,
    session_secret: str,
    *,
    source: str | None = None,
    utm: dict[str, str] | None = None,
) -> tuple[ReferralLink, str]:
    link = await db.scalar(select(ReferralLink).where(ReferralLink.code == normalize_code(code)))
    if (
        not link
        or link.status is not ReferralLinkStatus.active
        or (link.expires_at and link.expires_at <= now())
    ):
        raise ReferralError("Referral link is unavailable")
    program = await db.get(ReferralProgram, link.program_id)
    policy = await db.get(ReferralCommissionPolicy, link.policy_id)
    if (
        not program
        or not policy
        or program.status is not ReferralProgramStatus.active
        or policy.status is not ReferralPolicyStatus.active
    ):
        raise ReferralError("Referral link is unavailable")
    if program.actor_type is ReferralActorType.affiliate_partner:
        partner = await db.get(AffiliatePartner, program.affiliate_partner_id)
        if not partner or partner.status is not AffiliatePartnerStatus.active:
            raise ReferralError("Referral link is unavailable")
    session_hash = hashlib.sha256(session_secret.encode()).hexdigest()
    cutoff = now() - timedelta(seconds=_TOUCH_DEDUPE_SECONDS)
    touch = await db.scalar(
        select(ReferralTouch)
        .where(
            ReferralTouch.referral_link_id == link.id,
            ReferralTouch.session_hash == session_hash,
            ReferralTouch.occurred_at >= cutoff,
        )
        .order_by(ReferralTouch.occurred_at.desc())
    )
    if not touch:
        clean_utm = {
            key: value[:120]
            for key, value in (utm or {}).items()
            if key in {"source", "medium", "campaign", "content"} and value
        }
        touch = ReferralTouch(
            referral_link_id=link.id,
            session_hash=session_hash,
            destination_path=link.destination_path,
            occurred_at=now(),
            source=(source or link.source or "")[:80] or None,
            utm=clean_utm,
        )
        db.add(touch)
        await db.flush()
    expires_at = (
        min(link.expires_at, now() + timedelta(days=policy.attribution_window_days))
        if link.expires_at
        else now() + timedelta(days=policy.attribution_window_days)
    )
    return link, _sign({"touch_id": str(touch.id), "expires_at": int(expires_at.timestamp())})


async def snapshot_signup_attribution(
    db: AsyncSession, user: User, token: str | None
) -> SignupAttribution | None:
    if not token or await db.scalar(
        select(SignupAttribution).where(SignupAttribution.user_id == user.id)
    ):
        return None
    payload = _verify(token)
    if not payload:
        return None
    try:
        touch = await db.get(ReferralTouch, UUID(str(payload["touch_id"])))
    except ValueError:
        return None
    if not touch or not touch.eligible:
        return None
    candidates = (
        await db.scalars(
            select(ReferralTouch)
            .where(
                ReferralTouch.session_hash == touch.session_hash,
                ReferralTouch.eligible.is_(True),
                ReferralTouch.occurred_at <= now(),
            )
            .order_by(ReferralTouch.occurred_at.desc())
        )
    ).all()
    effective: (
        tuple[ReferralTouch, ReferralLink, ReferralCommissionPolicy, ReferralProgram] | None
    ) = None
    first: ReferralTouch | None = None
    for candidate in candidates:
        candidate_link = await db.get(ReferralLink, candidate.referral_link_id)
        candidate_policy = (
            await db.get(ReferralCommissionPolicy, candidate_link.policy_id)
            if candidate_link
            else None
        )
        candidate_program = (
            await db.get(ReferralProgram, candidate_link.program_id) if candidate_link else None
        )
        if (
            not candidate_link
            or not candidate_policy
            or not candidate_program
            or candidate_link.status is not ReferralLinkStatus.active
            or candidate_policy.status is not ReferralPolicyStatus.active
            or candidate_program.status is not ReferralProgramStatus.active
            or (candidate_link.expires_at and candidate_link.expires_at <= now())
            or candidate.occurred_at
            < now() - timedelta(days=candidate_policy.attribution_window_days)
        ):
            continue
        if effective is None:
            effective = (candidate, candidate_link, candidate_policy, candidate_program)
        first = candidate
    if not effective:
        return None
    touch, link, policy, program = effective
    # A newly-created account can never validly earn through its own program.
    # Existing accounts are not re-attributed, so this deterministic check is
    # sufficient for the Phase 10 signup boundary.
    owner_creator = (
        await db.get(CreatorProfile, program.owner_creator_id) if program.owner_creator_id else None
    )
    if program.owner_user_id == user.id or (owner_creator and owner_creator.user_id == user.id):
        touch.eligible = False
        return None
    attribution = SignupAttribution(
        user_id=user.id,
        first_touch_id=(first or touch).id,
        last_touch_id=touch.id,
        effective_link_id=link.id,
        policy_id=policy.id,
        policy_snapshot=policy_snapshot(policy),
        attributed_at=now(),
    )
    db.add(attribution)
    await record_event(
        db,
        "referral.signup_attributed",
        actor_user_id=user.id,
        target_type="signup_attribution",
        target_id=str(attribution.id),
        metadata={"referral_link_id": str(link.id), "policy_version": policy.version},
    )
    return attribution


async def revenue_allocation(
    db: AsyncSession,
    *,
    buyer_user_id: UUID,
    revenue_type: str,
    currency: str,
    platform_fee_minor: int,
    occurred_at: datetime,
) -> tuple[list[tuple[object, object, int]], dict[str, object] | None]:
    """Build a platform-funded referral allocation for one settled event.

    The returned entry is intentionally additive to the financial event: it
    debits none of the creator-side distributable and reduces only the
    platform-revenue credit by the referral amount.  The immutable signup
    policy snapshot, rather than a mutable current program configuration,
    controls the allocation.
    """
    if platform_fee_minor < 0:
        raise ReferralError("Platform fee cannot be negative")
    attribution = await db.scalar(
        select(SignupAttribution)
        .where(SignupAttribution.user_id == buyer_user_id)
        .with_for_update()
    )
    if not attribution:
        return [], None
    snapshot = attribution.policy_snapshot
    eligible_types = snapshot.get("eligible_revenue_types", [])
    if not isinstance(eligible_types, list) or revenue_type not in eligible_types:
        return [], None
    basis_points = snapshot.get("basis_points")
    if not isinstance(basis_points, int) or not 0 <= basis_points <= 10_000:
        raise ReferralError("Referral policy snapshot is invalid")
    if revenue_type == "subscription":
        window = await db.scalar(
            select(ReferralSubscriptionRewardWindow)
            .where(ReferralSubscriptionRewardWindow.signup_attribution_id == attribution.id)
            .with_for_update()
        )
        if not window:
            window_days = snapshot.get("subscription_reward_window_days")
            if not isinstance(window_days, int) or window_days <= 0:
                raise ReferralError("Referral subscription reward window is invalid")
            window = ReferralSubscriptionRewardWindow(
                signup_attribution_id=attribution.id,
                policy_id=attribution.policy_id,
                first_successful_payment_at=occurred_at,
                reward_window_ends_at=occurred_at + timedelta(days=window_days),
            )
            db.add(window)
            await db.flush()
        if occurred_at >= window.reward_window_ends_at:
            return [], None
    amount_minor = platform_fee_minor * basis_points // 10_000
    if amount_minor > platform_fee_minor:
        raise ReferralError("Referral reward exceeds available platform fee")
    if not amount_minor:
        return [], None
    link = await db.get(ReferralLink, attribution.effective_link_id)
    if not link:
        raise ReferralError("Referral attribution link is unavailable")
    program = await db.get(ReferralProgram, link.program_id)
    if not program or program.program_type is ReferralProgramType.creator_creator_referral:
        return [], None
    if program.actor_type is ReferralActorType.affiliate_partner:
        partner = await db.get(AffiliatePartner, program.affiliate_partner_id)
        if not partner or partner.status is not AffiliatePartnerStatus.active:
            return [], None

    # Import lazily: finance is the ledger owner and calls this resolver while
    # settling a payment, so an import at module load would form a cycle.
    from app.finance.service import _account
    from app.models.finance import LedgerAccountKind, LedgerDirection

    account_kind: LedgerAccountKind
    account_kwargs: dict[str, UUID]
    if program.actor_type is ReferralActorType.creator and program.owner_creator_id:
        account_kind = LedgerAccountKind.referrer_pending
        account_kwargs = {"owner_creator_id": program.owner_creator_id}
    elif program.actor_type is ReferralActorType.user and program.owner_user_id:
        account_kind = LedgerAccountKind.referrer_pending
        account_kwargs = {"owner_user_id": program.owner_user_id}
    elif program.actor_type is ReferralActorType.affiliate_partner and program.affiliate_partner_id:
        account_kind = LedgerAccountKind.affiliate_pending
        account_kwargs = {"owner_affiliate_partner_id": program.affiliate_partner_id}
    else:
        # Platform campaigns have no external beneficiary.  Malformed programs
        # fail closed instead of silently moving platform funds to an unknown
        # account.
        if program.actor_type is ReferralActorType.platform_campaign:
            return [], None
        raise ReferralError("Referral program has no valid beneficiary")
    account = await _account(db, account_kind, currency, **account_kwargs)
    return [
        (account, LedgerDirection.credit, amount_minor),
    ], {
        "signup_attribution_id": attribution.id,
        "policy_id": attribution.policy_id,
        "beneficiary_actor_type": program.actor_type,
        "beneficiary_user_id": program.owner_user_id,
        "beneficiary_creator_id": program.owner_creator_id,
        "beneficiary_affiliate_partner_id": program.affiliate_partner_id,
        "revenue_type": revenue_type,
        "currency": currency,
        "platform_fee_minor": platform_fee_minor,
        "amount_minor": amount_minor,
        "policy_snapshot": dict(snapshot),
        "allocated_at": occurred_at,
    }


async def record_revenue_allocation(
    db: AsyncSession,
    *,
    source_ledger_transaction_id: UUID,
    allocation: dict[str, object] | None,
) -> ReferralCommissionAllocation | None:
    """Persist the immutable allocation snapshot once its source ledger exists."""
    if not allocation:
        return None
    existing = await db.scalar(
        select(ReferralCommissionAllocation).where(
            ReferralCommissionAllocation.source_ledger_transaction_id
            == source_ledger_transaction_id
        )
    )
    if existing:
        return existing
    row = ReferralCommissionAllocation(
        source_ledger_transaction_id=source_ledger_transaction_id,
        signup_attribution_id=allocation["signup_attribution_id"],
        policy_id=allocation["policy_id"],
        beneficiary_actor_type=allocation["beneficiary_actor_type"],
        beneficiary_user_id=allocation["beneficiary_user_id"],
        beneficiary_creator_id=allocation["beneficiary_creator_id"],
        beneficiary_affiliate_partner_id=allocation["beneficiary_affiliate_partner_id"],
        revenue_type=allocation["revenue_type"],
        currency=allocation["currency"],
        platform_fee_minor=allocation["platform_fee_minor"],
        amount_minor=allocation["amount_minor"],
        policy_snapshot=allocation["policy_snapshot"],
        allocated_at=allocation["allocated_at"],
    )
    db.add(row)
    await db.flush()
    return row


async def reversal_entries(
    db: AsyncSession, source_ledger_transaction_id: UUID
) -> tuple[list[tuple[object, object, int]], ReferralCommissionAllocation | None]:
    """Return the exact historical referral entry to reverse, if any.

    Reversal never resolves today's policy, attribution, affiliation, or
    beneficiary ownership.  It follows the immutable allocation row attached
    to the original financial event.
    """
    allocation = await db.scalar(
        select(ReferralCommissionAllocation)
        .where(
            ReferralCommissionAllocation.source_ledger_transaction_id
            == source_ledger_transaction_id
        )
        .with_for_update()
    )
    if not allocation or allocation.reversed_at:
        return [], allocation
    from app.finance.service import _account
    from app.models.finance import LedgerAccountKind, LedgerDirection

    if allocation.beneficiary_actor_type is ReferralActorType.affiliate_partner:
        kind = (
            LedgerAccountKind.affiliate_available
            if allocation.released_at
            else LedgerAccountKind.affiliate_pending
        )
        if not allocation.beneficiary_affiliate_partner_id:
            raise ReferralError("Historical affiliate allocation is incomplete")
        account = await _account(
            db,
            kind,
            allocation.currency,
            owner_affiliate_partner_id=allocation.beneficiary_affiliate_partner_id,
        )
    else:
        kind = (
            LedgerAccountKind.referrer_available
            if allocation.released_at
            else LedgerAccountKind.referrer_pending
        )
        if allocation.beneficiary_creator_id:
            account = await _account(
                db,
                kind,
                allocation.currency,
                owner_creator_id=allocation.beneficiary_creator_id,
            )
        elif allocation.beneficiary_user_id:
            account = await _account(
                db,
                kind,
                allocation.currency,
                owner_user_id=allocation.beneficiary_user_id,
            )
        else:
            raise ReferralError("Historical referral allocation is incomplete")
    return [(account, LedgerDirection.debit, allocation.amount_minor)], allocation


async def release_entries(
    db: AsyncSession, source_ledger_transaction_id: UUID
) -> tuple[list[tuple[object, object, int]], ReferralCommissionAllocation | None]:
    """Move a marketplace referral's original pending allocation once only."""
    allocation = await db.scalar(
        select(ReferralCommissionAllocation)
        .where(
            ReferralCommissionAllocation.source_ledger_transaction_id
            == source_ledger_transaction_id
        )
        .with_for_update()
    )
    if not allocation or allocation.released_at or allocation.reversed_at:
        return [], allocation
    from app.finance.service import _account
    from app.models.finance import LedgerAccountKind, LedgerDirection

    if allocation.beneficiary_actor_type is ReferralActorType.affiliate_partner:
        if not allocation.beneficiary_affiliate_partner_id:
            raise ReferralError("Historical affiliate allocation is incomplete")
        pending = await _account(
            db,
            LedgerAccountKind.affiliate_pending,
            allocation.currency,
            owner_affiliate_partner_id=allocation.beneficiary_affiliate_partner_id,
        )
        available = await _account(
            db,
            LedgerAccountKind.affiliate_available,
            allocation.currency,
            owner_affiliate_partner_id=allocation.beneficiary_affiliate_partner_id,
        )
    elif allocation.beneficiary_creator_id:
        pending = await _account(
            db,
            LedgerAccountKind.referrer_pending,
            allocation.currency,
            owner_creator_id=allocation.beneficiary_creator_id,
        )
        available = await _account(
            db,
            LedgerAccountKind.referrer_available,
            allocation.currency,
            owner_creator_id=allocation.beneficiary_creator_id,
        )
    elif allocation.beneficiary_user_id:
        pending = await _account(
            db,
            LedgerAccountKind.referrer_pending,
            allocation.currency,
            owner_user_id=allocation.beneficiary_user_id,
        )
        available = await _account(
            db,
            LedgerAccountKind.referrer_available,
            allocation.currency,
            owner_user_id=allocation.beneficiary_user_id,
        )
    else:
        raise ReferralError("Historical referral allocation is incomplete")
    return [
        (pending, LedgerDirection.debit, allocation.amount_minor),
        (available, LedgerDirection.credit, allocation.amount_minor),
    ], allocation


async def affiliate_dashboard_allocations(
    db: AsyncSession, authenticated_user_id: UUID
) -> list[ReferralCommissionAllocation]:
    """Return allocations only for affiliate partners owned by this account."""
    partner_ids = list(
        await db.scalars(
            select(AffiliatePartner.id).where(
                AffiliatePartner.owner_user_id == authenticated_user_id
            )
        )
    )
    if not partner_ids:
        return []
    return list(
        await db.scalars(
            select(ReferralCommissionAllocation)
            .where(ReferralCommissionAllocation.beneficiary_affiliate_partner_id.in_(partner_ids))
            .order_by(ReferralCommissionAllocation.allocated_at.desc())
        )
    )


async def dashboard(db: AsyncSession, authenticated_user_id: UUID) -> dict[str, object]:
    """Build an ownership-scoped referral dashboard from immutable allocations.

    The totals deliberately use allocation lifecycle snapshots, which are tied
    to their source ledger transaction.  They never consult a current program
    policy, so changing a policy cannot rewrite a previously-earned dashboard.
    """
    creator = await db.scalar(
        select(CreatorProfile).where(CreatorProfile.user_id == authenticated_user_id)
    )
    affiliate_ids = list(
        await db.scalars(
            select(AffiliatePartner.id).where(AffiliatePartner.owner_user_id == authenticated_user_id)
        )
    )
    allocation_conditions = [
        ReferralCommissionAllocation.beneficiary_user_id == authenticated_user_id
    ]
    program_conditions = [ReferralProgram.owner_user_id == authenticated_user_id]
    if creator:
        allocation_conditions.append(
            ReferralCommissionAllocation.beneficiary_creator_id == creator.id
        )
        program_conditions.append(ReferralProgram.owner_creator_id == creator.id)
    if affiliate_ids:
        allocation_conditions.append(
            ReferralCommissionAllocation.beneficiary_affiliate_partner_id.in_(affiliate_ids)
        )
        program_conditions.append(ReferralProgram.affiliate_partner_id.in_(affiliate_ids))

    allocations = list(
        await db.scalars(
            select(ReferralCommissionAllocation)
            .where(or_(*allocation_conditions))
            .order_by(ReferralCommissionAllocation.allocated_at.desc())
        )
    )
    programs = list(
        await db.scalars(
            select(ReferralProgram).where(or_(*program_conditions)).order_by(ReferralProgram.created_at)
        )
    )
    program_ids = [program.id for program in programs]
    links = []
    if program_ids:
        links = list(
            await db.scalars(
                select(ReferralLink)
                .where(ReferralLink.program_id.in_(program_ids))
                .order_by(ReferralLink.created_at.desc())
            )
        )
    conversions_by_link = {
        link_id: count
        for link_id, count in (
            await db.execute(
                select(SignupAttribution.effective_link_id, func.count(SignupAttribution.id))
                .where(SignupAttribution.effective_link_id.in_([link.id for link in links]))
                .group_by(SignupAttribution.effective_link_id)
            )
        ).all()
    } if links else {}

    totals: dict[str, dict[str, int]] = {}
    for allocation in allocations:
        bucket = totals.setdefault(
            allocation.currency, {"pending_amount_minor": 0, "available_amount_minor": 0, "reversed_amount_minor": 0}
        )
        if allocation.reversed_at:
            bucket["reversed_amount_minor"] += allocation.amount_minor
        elif allocation.released_at:
            bucket["available_amount_minor"] += allocation.amount_minor
        else:
            bucket["pending_amount_minor"] += allocation.amount_minor

    return {
        "totals_by_currency": totals,
        "allocations": [
            {
                "id": str(row.id),
                "revenue_type": row.revenue_type,
                "currency": row.currency,
                "amount_minor": row.amount_minor,
                "platform_fee_minor": row.platform_fee_minor,
                "allocated_at": row.allocated_at,
                "released_at": row.released_at,
                "reversed_at": row.reversed_at,
            }
            for row in allocations
        ],
        "links": [
            {
                "public_id": link.public_id,
                "code": link.code,
                "destination_path": link.destination_path,
                "status": link.status.value,
                "conversions": conversions_by_link.get(link.id, 0),
            }
            for link in links
        ],
    }
