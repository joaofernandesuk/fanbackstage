"""Add Phase 8 groups, memberships, contracts, and permission grants."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260821_0012"
down_revision = "20260821_0011"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    for name, values in {
        "group_status": "active, suspended, disabled",
        "group_manager_role": "owner, admin, manager, operator",
        "group_membership_status": "invited, pending_acceptance, active, leaving, left, removed, suspended",
        "group_contract_status": "proposed, active, rejected, ended, expired",
        "group_permission": "view_profile, edit_profile, manage_content, publish_posts, manage_subscriptions, manage_promotions, manage_messaging, manage_live_settings, view_analytics, view_earnings",
    }.items():
        op.execute(
            f"CREATE TYPE {name} AS ENUM ({', '.join(repr(v.strip()) for v in values.split(','))})"
        )
    op.create_table(
        "groups",
        sa.Column("public_id", sa.String(48), nullable=False, unique=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False, unique=True),
        sa.Column("description", sa.Text()),
        sa.Column(
            "status",
            postgresql.ENUM(name="group_status", create_type=False),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "owner_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("default_creator_basis_points", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "default_creator_basis_points >= 0 AND default_creator_basis_points <= 10000",
            name="ck_group_default_creator_bps",
        ),
    )
    op.create_table(
        "group_manager_memberships",
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "role", postgresql.ENUM(name="group_manager_role", create_type=False), nullable=False
        ),
        *_timestamps(),
        sa.UniqueConstraint("group_id", "user_id", name="uq_group_manager_member"),
    )
    op.create_table(
        "group_creator_memberships",
        sa.Column(
            "group_id", sa.Uuid(), sa.ForeignKey("groups.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="group_membership_status", create_type=False),
            nullable=False,
        ),
        sa.Column("affiliation_public", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("joined_at", sa.DateTime(timezone=True)),
        sa.Column("left_at", sa.DateTime(timezone=True)),
        *_timestamps(),
    )
    op.create_index(
        "ix_group_creator_active", "group_creator_memberships", ["creator_id", "status"]
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_one_active_group_per_creator ON group_creator_memberships (creator_id) WHERE status IN ('invited', 'pending_acceptance', 'active', 'leaving')"
    )
    op.create_table(
        "group_contracts",
        sa.Column(
            "membership_id",
            sa.Uuid(),
            sa.ForeignKey("group_creator_memberships.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("creator_basis_points", sa.Integer(), nullable=False),
        sa.Column("group_basis_points", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="group_contract_status", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "proposed_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("rejected_at", sa.DateTime(timezone=True)),
        sa.Column("effective_from", sa.DateTime(timezone=True)),
        sa.Column("effective_until", sa.DateTime(timezone=True)),
        sa.Column(
            "supersedes_contract_id",
            sa.Uuid(),
            sa.ForeignKey("group_contracts.id", ondelete="RESTRICT"),
        ),
        *_timestamps(),
        sa.CheckConstraint(
            "creator_basis_points >= 0 AND group_basis_points >= 0 AND creator_basis_points + group_basis_points = 10000",
            name="ck_group_contract_split",
        ),
        sa.UniqueConstraint(
            "membership_id", "version", name="uq_group_contract_membership_version"
        ),
    )
    op.create_table(
        "group_permission_grants",
        sa.Column(
            "membership_id",
            sa.Uuid(),
            sa.ForeignKey("group_creator_memberships.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "manager_membership_id",
            sa.Uuid(),
            sa.ForeignKey("group_manager_memberships.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "permission",
            postgresql.ENUM(name="group_permission", create_type=False),
            nullable=False,
        ),
        *_timestamps(),
        sa.UniqueConstraint(
            "membership_id", "manager_membership_id", "permission", name="uq_group_permission_grant"
        ),
    )


def downgrade() -> None:
    op.drop_table("group_permission_grants")
    op.drop_table("group_contracts")
    op.drop_index("uq_one_active_group_per_creator", table_name="group_creator_memberships")
    op.drop_table("group_creator_memberships")
    op.drop_table("group_manager_memberships")
    op.drop_table("groups")
    for name in (
        "group_permission",
        "group_contract_status",
        "group_membership_status",
        "group_manager_role",
        "group_status",
    ):
        op.execute(f"DROP TYPE {name}")
