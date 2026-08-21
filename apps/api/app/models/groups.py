"""Agency/group contracts and delegated operational authority."""

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
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamped, UUIDPrimaryKey


class GroupStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"
    disabled = "disabled"


class GroupManagerRole(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    manager = "manager"
    operator = "operator"


class GroupMembershipStatus(str, enum.Enum):
    invited = "invited"
    pending_acceptance = "pending_acceptance"
    active = "active"
    leaving = "leaving"
    left = "left"
    removed = "removed"
    suspended = "suspended"


class GroupContractStatus(str, enum.Enum):
    proposed = "proposed"
    active = "active"
    rejected = "rejected"
    ended = "ended"
    expired = "expired"


class GroupPermission(str, enum.Enum):
    view_profile = "view_profile"
    edit_profile = "edit_profile"
    manage_content = "manage_content"
    publish_posts = "publish_posts"
    manage_subscriptions = "manage_subscriptions"
    manage_promotions = "manage_promotions"
    manage_messaging = "manage_messaging"
    manage_live_settings = "manage_live_settings"
    view_analytics = "view_analytics"
    view_earnings = "view_earnings"


class Group(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "groups"
    __table_args__ = (
        CheckConstraint(
            "default_creator_basis_points >= 0 AND default_creator_basis_points <= 10000",
            name="ck_group_default_creator_bps",
        ),
    )
    public_id: Mapped[str] = mapped_column(String(48), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[GroupStatus] = mapped_column(
        Enum(GroupStatus, name="group_status"), default=GroupStatus.active, index=True
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    default_creator_basis_points: Mapped[int] = mapped_column(Integer)


class GroupManagerMembership(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "group_manager_memberships"
    __table_args__ = (UniqueConstraint("group_id", "user_id", name="uq_group_manager_member"),)
    group_id: Mapped[UUID] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    role: Mapped[GroupManagerRole] = mapped_column(
        Enum(GroupManagerRole, name="group_manager_role")
    )


class GroupCreatorMembership(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "group_creator_memberships"
    __table_args__ = (Index("ix_group_creator_active", "creator_id", "status"),)
    group_id: Mapped[UUID] = mapped_column(ForeignKey("groups.id", ondelete="RESTRICT"), index=True)
    creator_id: Mapped[UUID] = mapped_column(
        ForeignKey("creator_profiles.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[GroupMembershipStatus] = mapped_column(
        Enum(GroupMembershipStatus, name="group_membership_status"), index=True
    )
    affiliation_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GroupContract(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "group_contracts"
    __table_args__ = (
        CheckConstraint(
            "creator_basis_points + group_basis_points = 10000", name="ck_group_contract_split"
        ),
        UniqueConstraint("membership_id", "version", name="uq_group_contract_membership_version"),
    )
    membership_id: Mapped[UUID] = mapped_column(
        ForeignKey("group_creator_memberships.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    creator_basis_points: Mapped[int] = mapped_column(Integer)
    group_basis_points: Mapped[int] = mapped_column(Integer)
    status: Mapped[GroupContractStatus] = mapped_column(
        Enum(GroupContractStatus, name="group_contract_status"), index=True
    )
    proposed_by_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rejected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_contract_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("group_contracts.id", ondelete="RESTRICT")
    )


class GroupPermissionGrant(UUIDPrimaryKey, Timestamped, Base):
    __tablename__ = "group_permission_grants"
    __table_args__ = (
        UniqueConstraint(
            "membership_id", "manager_membership_id", "permission", name="uq_group_permission_grant"
        ),
    )
    membership_id: Mapped[UUID] = mapped_column(
        ForeignKey("group_creator_memberships.id", ondelete="CASCADE"), index=True
    )
    manager_membership_id: Mapped[UUID] = mapped_column(
        ForeignKey("group_manager_memberships.id", ondelete="CASCADE"), index=True
    )
    permission: Mapped[GroupPermission] = mapped_column(
        Enum(GroupPermission, name="group_permission")
    )
