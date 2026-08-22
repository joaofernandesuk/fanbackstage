import enum
from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamped, UUIDPrimaryKey


class ReferralActorType(str, enum.Enum):
    creator = "creator"
    user = "user"
    affiliate_partner = "affiliate_partner"
    platform_campaign = "platform_campaign"


class ReferralProgramType(str, enum.Enum):
    creator_buyer_referral = "creator_buyer_referral"
    user_user_referral = "user_user_referral"
    affiliate_referral = "affiliate_referral"
    creator_creator_referral = "creator_creator_referral"


class ReferralProgramStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    ended = "ended"


class ReferralLinkStatus(str, enum.Enum):
    active = "active"
    disabled = "disabled"
    expired = "expired"


class AffiliatePartnerStatus(str, enum.Enum):
    active = "active"
    paused = "paused"
    suspended = "suspended"
    terminated = "terminated"


class ReferralPolicyStatus(str, enum.Enum):
    active = "active"
    superseded = "superseded"
    ended = "ended"


class AffiliatePartner(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "affiliate_partners"

    public_id: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[AffiliatePartnerStatus] = mapped_column(
        Enum(AffiliatePartnerStatus, name="affiliate_partner_status"), index=True
    )
    owner_contact_reference: Mapped[str | None] = mapped_column(String(255))
    external_reference: Mapped[str | None] = mapped_column(String(255), unique=True)
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReferralProgram(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "referral_programs"

    public_id: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    actor_type: Mapped[ReferralActorType] = mapped_column(
        Enum(ReferralActorType, name="referral_actor_type"), index=True
    )
    owner_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    owner_creator_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    affiliate_partner_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("affiliate_partners.id", ondelete="RESTRICT"), index=True
    )
    program_type: Mapped[ReferralProgramType] = mapped_column(
        Enum(ReferralProgramType, name="referral_program_type"), index=True
    )
    status: Mapped[ReferralProgramStatus] = mapped_column(
        Enum(ReferralProgramStatus, name="referral_program_status"), index=True
    )
    terms_reference: Mapped[str | None] = mapped_column(String(255))
    campaign_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class ReferralCommissionPolicy(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "referral_commission_policies"
    __table_args__ = (
        CheckConstraint(
            "basis_points >= 0 AND basis_points <= 10000", name="ck_referral_policy_bps"
        ),
        CheckConstraint(
            "attribution_window_days > 0", name="ck_referral_policy_attribution_window"
        ),
        CheckConstraint(
            "subscription_reward_window_days > 0", name="ck_referral_policy_subscription_window"
        ),
        UniqueConstraint("program_id", "version", name="uq_referral_policy_program_version"),
    )

    public_id: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    program_id: Mapped[UUID] = mapped_column(
        ForeignKey("referral_programs.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    basis_points: Mapped[int] = mapped_column(Integer)
    attribution_window_days: Mapped[int] = mapped_column(Integer, default=30)
    subscription_reward_window_days: Mapped[int] = mapped_column(Integer, default=90)
    eligible_revenue_types: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    status: Mapped[ReferralPolicyStatus] = mapped_column(
        Enum(ReferralPolicyStatus, name="referral_policy_status"), index=True
    )
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ReferralLink(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "referral_links"
    __table_args__ = (UniqueConstraint("code", name="uq_referral_link_code"),)

    public_id: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    program_id: Mapped[UUID] = mapped_column(
        ForeignKey("referral_programs.id", ondelete="RESTRICT"), index=True
    )
    policy_id: Mapped[UUID] = mapped_column(
        ForeignKey("referral_commission_policies.id", ondelete="RESTRICT"), index=True
    )
    code: Mapped[str] = mapped_column(String(64), index=True)
    destination_path: Mapped[str] = mapped_column(String(512))
    status: Mapped[ReferralLinkStatus] = mapped_column(
        Enum(ReferralLinkStatus, name="referral_link_status"), index=True
    )
    source: Mapped[str | None] = mapped_column(String(80))
    campaign_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class ReferralTouch(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "referral_touches"
    __table_args__ = (Index("ix_referral_touches_session_occurred", "session_hash", "occurred_at"),)

    referral_link_id: Mapped[UUID] = mapped_column(
        ForeignKey("referral_links.id", ondelete="RESTRICT"), index=True
    )
    session_hash: Mapped[str] = mapped_column(String(64), index=True)
    destination_path: Mapped[str] = mapped_column(String(512))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    eligible: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str | None] = mapped_column(String(80))
    utm: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class SignupAttribution(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "signup_attributions"
    __table_args__ = (UniqueConstraint("user_id", name="uq_signup_attribution_user"),)

    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    first_touch_id: Mapped[UUID] = mapped_column(
        ForeignKey("referral_touches.id", ondelete="RESTRICT")
    )
    last_touch_id: Mapped[UUID] = mapped_column(
        ForeignKey("referral_touches.id", ondelete="RESTRICT")
    )
    effective_link_id: Mapped[UUID] = mapped_column(
        ForeignKey("referral_links.id", ondelete="RESTRICT"), index=True
    )
    policy_id: Mapped[UUID] = mapped_column(
        ForeignKey("referral_commission_policies.id", ondelete="RESTRICT")
    )
    policy_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    attributed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ReferralCommissionAllocation(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "referral_commission_allocations"
    __table_args__ = (
        CheckConstraint("amount_minor >= 0", name="ck_referral_allocation_nonnegative_amount"),
        CheckConstraint(
            "amount_minor <= platform_fee_minor", name="ck_referral_allocation_within_platform_fee"
        ),
        CheckConstraint(
            "(beneficiary_actor_type = 'creator' AND beneficiary_creator_id IS NOT NULL "
            "AND beneficiary_user_id IS NULL AND beneficiary_affiliate_partner_id IS NULL) OR "
            "(beneficiary_actor_type = 'user' AND beneficiary_user_id IS NOT NULL "
            "AND beneficiary_creator_id IS NULL AND beneficiary_affiliate_partner_id IS NULL) OR "
            "(beneficiary_actor_type = 'affiliate_partner' "
            "AND beneficiary_affiliate_partner_id IS NOT NULL AND beneficiary_creator_id IS NULL "
            "AND beneficiary_user_id IS NULL)",
            name="ck_referral_allocation_beneficiary",
        ),
        UniqueConstraint(
            "source_ledger_transaction_id", name="uq_referral_allocation_source_ledger"
        ),
    )

    source_ledger_transaction_id: Mapped[UUID] = mapped_column(
        ForeignKey("ledger_transactions.id", ondelete="RESTRICT"), index=True
    )
    signup_attribution_id: Mapped[UUID] = mapped_column(
        ForeignKey("signup_attributions.id", ondelete="RESTRICT"), index=True
    )
    policy_id: Mapped[UUID] = mapped_column(
        ForeignKey("referral_commission_policies.id", ondelete="RESTRICT")
    )
    beneficiary_actor_type: Mapped[ReferralActorType] = mapped_column(
        Enum(ReferralActorType, name="referral_actor_type"), index=True
    )
    beneficiary_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    beneficiary_creator_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    beneficiary_affiliate_partner_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("affiliate_partners.id", ondelete="RESTRICT"), index=True
    )
    revenue_type: Mapped[str] = mapped_column(String(64), index=True)
    currency: Mapped[str] = mapped_column(String(3))
    platform_fee_minor: Mapped[int] = mapped_column(Integer)
    amount_minor: Mapped[int] = mapped_column(Integer)
    policy_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    allocated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReferralSubscriptionRewardWindow(UUIDPrimaryKey, Timestamped, Base):
    """The immutable timestamp-based reward window for one attributed subscriber."""

    __tablename__ = "referral_subscription_reward_windows"
    __table_args__ = (
        UniqueConstraint(
            "signup_attribution_id", name="uq_referral_subscription_reward_window_attribution"
        ),
    )

    signup_attribution_id: Mapped[UUID] = mapped_column(
        ForeignKey("signup_attributions.id", ondelete="RESTRICT"), index=True
    )
    policy_id: Mapped[UUID] = mapped_column(
        ForeignKey("referral_commission_policies.id", ondelete="RESTRICT")
    )
    first_successful_payment_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    reward_window_ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
