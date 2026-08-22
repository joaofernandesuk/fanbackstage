"""Add the Phase 10 referral attribution foundation."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "5a8c53c69479"
down_revision = "20260822_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    affiliate_status = postgresql.ENUM(
        "active",
        "paused",
        "suspended",
        "terminated",
        name="affiliate_partner_status",
        create_type=False,
    )
    actor_type = postgresql.ENUM(
        "creator",
        "user",
        "affiliate_partner",
        "platform_campaign",
        name="referral_actor_type",
        create_type=False,
    )
    program_type = postgresql.ENUM(
        "creator_buyer_referral",
        "user_user_referral",
        "affiliate_referral",
        "creator_creator_referral",
        name="referral_program_type",
        create_type=False,
    )
    program_status = postgresql.ENUM(
        "active", "paused", "ended", name="referral_program_status", create_type=False
    )
    policy_status = postgresql.ENUM(
        "active", "superseded", "ended", name="referral_policy_status", create_type=False
    )
    link_status = postgresql.ENUM(
        "active", "disabled", "expired", name="referral_link_status", create_type=False
    )
    for enum in (
        affiliate_status,
        actor_type,
        program_type,
        program_status,
        policy_status,
        link_status,
    ):
        enum.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "affiliate_partners",
        sa.Column("public_id", sa.String(48), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("status", affiliate_status, nullable=False),
        sa.Column("owner_contact_reference", sa.String(255)),
        sa.Column("external_reference", sa.String(255)),
        sa.Column("suspended_at", sa.DateTime(timezone=True)),
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
        sa.UniqueConstraint("public_id", name="uq_affiliate_partners_public_id"),
        sa.UniqueConstraint("external_reference", name="uq_affiliate_partners_external_reference"),
    )
    op.create_index("ix_affiliate_partners_status", "affiliate_partners", ["status"])
    op.create_table(
        "referral_programs",
        sa.Column("public_id", sa.String(48), nullable=False),
        sa.Column("actor_type", actor_type, nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column(
            "owner_creator_id", sa.Uuid(), sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT")
        ),
        sa.Column(
            "affiliate_partner_id",
            sa.Uuid(),
            sa.ForeignKey("affiliate_partners.id", ondelete="RESTRICT"),
        ),
        sa.Column("program_type", program_type, nullable=False),
        sa.Column("status", program_status, nullable=False),
        sa.Column("terms_reference", sa.String(255)),
        sa.Column(
            "campaign_metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
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
        sa.UniqueConstraint("public_id", name="uq_referral_programs_public_id"),
    )
    for column in (
        "actor_type",
        "owner_user_id",
        "owner_creator_id",
        "affiliate_partner_id",
        "program_type",
        "status",
    ):
        op.create_index(f"ix_referral_programs_{column}", "referral_programs", [column])
    op.create_table(
        "referral_commission_policies",
        sa.Column("public_id", sa.String(48), nullable=False),
        sa.Column(
            "program_id",
            sa.Uuid(),
            sa.ForeignKey("referral_programs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("basis_points", sa.Integer(), nullable=False),
        sa.Column("attribution_window_days", sa.Integer(), nullable=False),
        sa.Column("subscription_reward_window_days", sa.Integer(), nullable=False),
        sa.Column(
            "eligible_revenue_types",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("status", policy_status, nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True)),
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
        sa.CheckConstraint(
            "basis_points >= 0 AND basis_points <= 10000", name="ck_referral_policy_bps"
        ),
        sa.CheckConstraint(
            "attribution_window_days > 0", name="ck_referral_policy_attribution_window"
        ),
        sa.CheckConstraint(
            "subscription_reward_window_days > 0", name="ck_referral_policy_subscription_window"
        ),
        sa.UniqueConstraint("public_id", name="uq_referral_commission_policies_public_id"),
        sa.UniqueConstraint("program_id", "version", name="uq_referral_policy_program_version"),
    )
    for column in ("program_id", "status", "effective_from", "effective_until"):
        op.create_index(
            f"ix_referral_commission_policies_{column}", "referral_commission_policies", [column]
        )
    op.create_table(
        "referral_links",
        sa.Column("public_id", sa.String(48), nullable=False),
        sa.Column(
            "program_id",
            sa.Uuid(),
            sa.ForeignKey("referral_programs.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "policy_id",
            sa.Uuid(),
            sa.ForeignKey("referral_commission_policies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("destination_path", sa.String(512), nullable=False),
        sa.Column("status", link_status, nullable=False),
        sa.Column("source", sa.String(80)),
        sa.Column(
            "campaign_metadata",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
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
        sa.UniqueConstraint("public_id", name="uq_referral_links_public_id"),
        sa.UniqueConstraint("code", name="uq_referral_link_code"),
    )
    for column in ("program_id", "policy_id", "code", "status", "expires_at"):
        op.create_index(f"ix_referral_links_{column}", "referral_links", [column])
    op.create_table(
        "referral_touches",
        sa.Column(
            "referral_link_id",
            sa.Uuid(),
            sa.ForeignKey("referral_links.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("destination_path", sa.String(512), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("eligible", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("source", sa.String(80)),
        sa.Column("utm", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
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
    )
    op.create_index("ix_referral_touches_link", "referral_touches", ["referral_link_id"])
    op.create_index(
        "ix_referral_touches_session_occurred", "referral_touches", ["session_hash", "occurred_at"]
    )
    op.create_table(
        "signup_attributions",
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column(
            "first_touch_id",
            sa.Uuid(),
            sa.ForeignKey("referral_touches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "last_touch_id",
            sa.Uuid(),
            sa.ForeignKey("referral_touches.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "effective_link_id",
            sa.Uuid(),
            sa.ForeignKey("referral_links.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "policy_id",
            sa.Uuid(),
            sa.ForeignKey("referral_commission_policies.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("policy_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("attributed_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.UniqueConstraint("user_id", name="uq_signup_attribution_user"),
    )
    op.create_index(
        "ix_signup_attributions_effective_link", "signup_attributions", ["effective_link_id"]
    )
    op.create_index(
        "ix_signup_attributions_attributed_at", "signup_attributions", ["attributed_at"]
    )


def downgrade() -> None:
    op.drop_table("signup_attributions")
    op.drop_table("referral_touches")
    op.drop_table("referral_links")
    op.drop_table("referral_commission_policies")
    op.drop_table("referral_programs")
    op.drop_table("affiliate_partners")
    for name in (
        "referral_link_status",
        "referral_policy_status",
        "referral_program_status",
        "referral_program_type",
        "referral_actor_type",
        "affiliate_partner_status",
    ):
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
