"""Add centralized Trust & Safety cases, reports, evidence and consent releases.

Revision ID: 20260823_0027
Revises: 20260823_0026
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260823_0027"
down_revision = "20260823_0026"
branch_labels = None
depends_on = None


def _enum(name: str, *values: str) -> postgresql.ENUM:
    return postgresql.ENUM(*values, name=name, create_type=False)


def upgrade() -> None:
    target = _enum(
        "ts_report_target_type",
        "user",
        "creator",
        "post",
        "comment",
        "media",
        "message",
        "live_room",
        "live_chat_message",
        "marketplace_listing",
        "marketplace_order",
        "featured_placement",
        "referral_affiliate",
    )
    reason = _enum(
        "ts_report_reason",
        "harassment",
        "spam",
        "impersonation",
        "non_consensual_content",
        "underage_concern",
        "illegal_content",
        "copyright",
        "scam_fraud",
        "prohibited_marketplace_item",
        "threat_abuse",
        "privacy",
        "other",
    )
    case_status = _enum(
        "moderation_case_status",
        "open",
        "triage",
        "investigating",
        "action_required",
        "resolved",
        "dismissed",
        "appealed",
        "reopened",
    )
    severity = _enum("moderation_severity", "low", "medium", "high", "critical")
    queue = _enum(
        "moderation_queue",
        "general",
        "content",
        "live",
        "marketplace",
        "consent",
        "fraud",
        "appeals",
        "urgent",
    )
    action = _enum(
        "moderation_action_type",
        "content_hide",
        "content_remove",
        "content_restore",
        "creator_discovery_hide",
        "creator_suspend",
        "creator_unsuspend",
        "user_suspend",
        "marketplace_listing_remove",
        "marketplace_selling_suspend",
        "live_terminate",
        "featured_placement_disable",
        "referral_affiliate_suspend",
        "temporary_containment",
        "warning",
        "no_action",
    )
    appeal = _enum(
        "appeal_status",
        "submitted",
        "under_review",
        "upheld",
        "overturned",
        "partially_overturned",
        "withdrawn",
        "expired",
    )
    release_type = _enum(
        "consent_release_type",
        "content_participation",
        "model_release",
        "co_performer_release",
        "live_participation",
    )
    release_status = _enum(
        "consent_release_status",
        "draft",
        "pending",
        "verified",
        "rejected",
        "expired",
        "revoked",
        "superseded",
    )
    for enum in (
        target,
        reason,
        case_status,
        severity,
        queue,
        action,
        appeal,
        release_type,
        release_status,
    ):
        postgresql.ENUM(*enum.enums, name=enum.name).create(op.get_bind(), checkfirst=True)
    op.create_table(
        "moderation_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column("public_id", sa.String(32), nullable=False, unique=True),
        sa.Column("primary_target_type", target, nullable=False),
        sa.Column("primary_target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", case_status, nullable=False, server_default="open"),
        sa.Column("severity", severity, nullable=False, server_default="medium"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("queue", queue, nullable=False, server_default="general"),
        sa.Column(
            "assigned_moderator_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.Column("decision_summary", sa.String(500)),
    )
    op.create_index(
        "ix_moderation_cases_queue_status_priority",
        "moderation_cases",
        ["queue", "status", "priority", "created_at"],
    )
    op.create_index(
        "ix_moderation_cases_target",
        "moderation_cases",
        ["primary_target_type", "primary_target_id"],
    )
    op.create_table(
        "trust_safety_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column(
            "reporter_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("target_type", target, nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", reason, nullable=False),
        sa.Column("details", sa.Text()),
        sa.Column(
            "context_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("moderation_cases.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "duplicate_of_report_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("trust_safety_reports.id", ondelete="RESTRICT"),
        ),
    )
    op.create_index(
        "ix_ts_report_target_reason_created",
        "trust_safety_reports",
        ["target_type", "target_id", "reason", "created_at"],
    )
    op.create_index("ix_ts_reports_case", "trust_safety_reports", ["case_id"])
    op.create_table(
        "moderation_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("moderation_cases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "snapshot", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("safe_reference", sa.String(512)),
        sa.Column("sensitive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
    )
    op.create_index("ix_moderation_evidence_case", "moderation_evidence", ["case_id"])
    op.create_table(
        "moderation_case_notes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("moderation_cases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "author_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
    )
    op.create_table(
        "moderation_actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column(
            "case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("moderation_cases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("action_type", action, nullable=False),
        sa.Column("target_type", target, nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column("reversed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "reversal_action_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("moderation_actions.id", ondelete="RESTRICT"),
        ),
        sa.UniqueConstraint(
            "case_id", "action_type", "target_type", "target_id", name="uq_moderation_action_replay"
        ),
    )
    op.create_table(
        "moderation_appeals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column(
            "moderation_case_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("moderation_cases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "moderation_action_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("moderation_actions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "appellant_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", appeal, nullable=False, server_default="submitted"),
        sa.Column("policy_deadline_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "reviewer_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("outcome", sa.String(500)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.Column("exceptional_late_review_reason", sa.String(500)),
    )
    op.create_index("ix_moderation_appeals_action", "moderation_appeals", ["moderation_action_id"])
    op.create_table(
        "consent_releases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column(
            "owner_creator_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("release_type", release_type, nullable=False),
        sa.Column("status", release_status, nullable=False, server_default="draft"),
        sa.Column("participant_reference", sa.String(255), nullable=False),
        sa.Column(
            "scope_snapshot",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("evidence_reference", sa.String(512)),
        sa.Column("effective_from", sa.DateTime(timezone=True)),
        sa.Column("effective_until", sa.DateTime(timezone=True)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column(
            "verified_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column(
            "supersedes_release_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("consent_releases.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_consent_releases_owner_status", "consent_releases", ["owner_creator_id", "status"]
    )
    op.create_table(
        "consent_release_contents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
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
        sa.Column(
            "consent_release_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("consent_releases.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "content_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("content_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("requires_consent", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("consent_release_id", "content_id", name="uq_consent_release_content"),
    )
    op.create_index(
        "ix_consent_release_contents_content", "consent_release_contents", ["content_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_consent_release_contents_content", table_name="consent_release_contents")
    op.drop_table("consent_release_contents")
    op.drop_index("ix_consent_releases_owner_status", table_name="consent_releases")
    op.drop_table("consent_releases")
    op.drop_index("ix_moderation_appeals_action", table_name="moderation_appeals")
    op.drop_table("moderation_appeals")
    op.drop_table("moderation_actions")
    op.drop_table("moderation_case_notes")
    op.drop_index("ix_moderation_evidence_case", table_name="moderation_evidence")
    op.drop_table("moderation_evidence")
    op.drop_index("ix_ts_reports_case", table_name="trust_safety_reports")
    op.drop_index("ix_ts_report_target_reason_created", table_name="trust_safety_reports")
    op.drop_table("trust_safety_reports")
    op.drop_index("ix_moderation_cases_target", table_name="moderation_cases")
    op.drop_index("ix_moderation_cases_queue_status_priority", table_name="moderation_cases")
    op.drop_table("moderation_cases")
    for name in (
        "consent_release_status",
        "consent_release_type",
        "appeal_status",
        "moderation_action_type",
        "moderation_queue",
        "moderation_severity",
        "moderation_case_status",
        "ts_report_reason",
        "ts_report_target_type",
    ):
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
