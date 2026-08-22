import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.core.config import get_settings
from app.models.identity import User
from app.models.referral import (
    AffiliatePartner,
    AffiliatePartnerStatus,
    ReferralActorType,
    ReferralCommissionPolicy,
    ReferralLink,
    ReferralLinkStatus,
    ReferralPolicyStatus,
    ReferralProgram,
    ReferralProgramStatus,
    ReferralProgramType,
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
    db: AsyncSession, actor: User, *, name: str, external_reference: str | None = None
) -> AffiliatePartner:
    partner = AffiliatePartner(
        public_id=opaque_id("ap"),
        name=name.strip(),
        status=AffiliatePartnerStatus.active,
        external_reference=external_reference,
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
        metadata={"public_id": partner.public_id},
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
    link = await db.get(ReferralLink, touch.referral_link_id)
    policy = await db.get(ReferralCommissionPolicy, link.policy_id) if link else None
    program = await db.get(ReferralProgram, link.program_id) if link else None
    if not link or not policy or not program or program.status is not ReferralProgramStatus.active:
        return None
    # A newly-created account can never validly earn through its own program.
    # Existing accounts are not re-attributed, so this deterministic check is
    # sufficient for the Phase 10 signup boundary.
    if program.owner_user_id == user.id:
        touch.eligible = False
        return None
    first_touch = await db.scalar(
        select(ReferralTouch)
        .where(
            ReferralTouch.session_hash == touch.session_hash,
            ReferralTouch.occurred_at <= touch.occurred_at,
        )
        .order_by(ReferralTouch.occurred_at.asc())
    )
    attribution = SignupAttribution(
        user_id=user.id,
        first_touch_id=(first_touch or touch).id,
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
