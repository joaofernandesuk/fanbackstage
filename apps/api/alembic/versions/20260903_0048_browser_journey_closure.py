"""Add profile media authority and private-session invitation consent.

Revision ID: 20260903_0048
Revises: 20260829_0044
Create Date: 2026-09-03
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260903_0048"
down_revision = "20260829_0044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "live_goals",
        sa.Column("starts_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    invitation_status = postgresql.ENUM(
        "not_required", "pending", "accepted", "declined", name="private_invitation_status"
    )
    invitation_status.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "private_session_requests",
        sa.Column(
            "invitation_status",
            invitation_status,
            server_default="not_required",
            nullable=False,
        ),
    )
    op.add_column(
        "private_session_requests",
        sa.Column("invitation_responded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_private_session_requests_invitation_status",
        "private_session_requests",
        ["invitation_status"],
    )
    op.execute(
        "UPDATE private_session_requests SET invitation_status = 'accepted' "
        "WHERE mode = 'two_to_one' AND invited_user_id IS NOT NULL"
    )

    op.create_table(
        "creator_profile_media",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("creator_profile_id", sa.UUID(), sa.ForeignKey("creator_profiles.id", ondelete="CASCADE"), nullable=False),
        sa.Column("media_asset_id", sa.UUID(), sa.ForeignKey("media_assets.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("focal_x", sa.Float(), server_default="0.5", nullable=False),
        sa.Column("focal_y", sa.Float(), server_default="0.5", nullable=False),
        sa.CheckConstraint("kind IN ('avatar', 'cover')", name="ck_creator_profile_media_kind"),
        sa.CheckConstraint("focal_x >= 0 AND focal_x <= 1", name="ck_creator_profile_media_focal_x"),
        sa.CheckConstraint("focal_y >= 0 AND focal_y <= 1", name="ck_creator_profile_media_focal_y"),
        sa.UniqueConstraint("creator_profile_id", "kind", name="uq_creator_profile_media_kind"),
        sa.UniqueConstraint("media_asset_id", name="uq_creator_profile_media_asset"),
    )
    op.create_index("ix_creator_profile_media_creator_profile_id", "creator_profile_media", ["creator_profile_id"])
    op.create_index("ix_creator_profile_media_media_asset_id", "creator_profile_media", ["media_asset_id"])


def downgrade() -> None:
    op.drop_column("live_goals", "starts_at")
    op.drop_table("creator_profile_media")
    op.drop_index("ix_private_session_requests_invitation_status", table_name="private_session_requests")
    op.drop_column("private_session_requests", "invitation_responded_at")
    op.drop_column("private_session_requests", "invitation_status")
    postgresql.ENUM(name="private_invitation_status").drop(op.get_bind(), checkfirst=True)
