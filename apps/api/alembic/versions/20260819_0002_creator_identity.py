"""creator identity and profile foundation"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260819_0002"
down_revision = "20260819_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE creator_status AS ENUM ('draft', 'pending_verification', 'pending_review', 'approved', 'rejected', 'suspended', 'disabled')"
    )
    op.execute(
        "CREATE TYPE verification_status AS ENUM ('not_started', 'pending', 'verified', 'failed', 'expired', 'needs_review')"
    )
    status = postgresql.ENUM(name="creator_status", create_type=False)
    verification = postgresql.ENUM(name="verification_status", create_type=False)
    op.create_table(
        "creator_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("username", sa.String(32), unique=True),
        sa.Column("display_name", sa.String(80)),
        sa.Column("bio", sa.Text()),
        sa.Column("avatar_reference", sa.String(255)),
        sa.Column("cover_reference", sa.String(255)),
        sa.Column("country_code", sa.String(2)),
        sa.Column("region", sa.String(80)),
        sa.Column("city", sa.String(80)),
        sa.Column("show_location", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("timezone", sa.String(64)),
        sa.Column("status", status, nullable=False, server_default="draft"),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("rejection_reason", sa.String(255)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index("ix_creator_profiles_status", "creator_profiles", ["status"])
    op.create_index(
        "uq_creator_profiles_username_lower",
        "creator_profiles",
        [sa.text("lower(username)")],
        unique=True,
    )
    op.create_table(
        "creator_verifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "creator_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("creator_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_reference", sa.String(255), nullable=False, unique=True),
        sa.Column("status", verification, nullable=False),
        sa.Column("adult_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_creator_verifications_creator_profile_id",
        "creator_verifications",
        ["creator_profile_id"],
    )
    op.create_index("ix_creator_verifications_status", "creator_verifications", ["status"])
    op.create_table(
        "creator_status_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "creator_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("creator_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("previous_status", status),
        sa.Column("new_status", status, nullable=False),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("reason", sa.String(255)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_creator_status_history_creator_profile_id",
        "creator_status_history",
        ["creator_profile_id"],
    )
    op.create_table(
        "creator_username_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("username", sa.String(32), nullable=False, unique=True),
        sa.Column(
            "creator_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("creator_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_index(
        "ix_creator_username_history_creator_profile_id",
        "creator_username_history",
        ["creator_profile_id"],
    )
    op.create_table(
        "creator_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(48), nullable=False, unique=True),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "creator_languages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(10), nullable=False, unique=True),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    op.create_table(
        "creator_profile_categories",
        sa.Column(
            "creator_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("creator_profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "category_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("creator_categories.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
    )
    op.create_table(
        "creator_profile_languages",
        sa.Column(
            "creator_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("creator_profiles.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "language_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("creator_languages.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
    )
    op.create_table(
        "creator_social_links",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "creator_profile_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("creator_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("label", sa.String(48), nullable=False),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.UniqueConstraint("creator_profile_id", "url"),
    )
    op.create_index(
        "ix_creator_social_links_creator_profile_id", "creator_social_links", ["creator_profile_id"]
    )


def downgrade() -> None:
    op.drop_table("creator_social_links")
    op.drop_table("creator_profile_languages")
    op.drop_table("creator_profile_categories")
    op.drop_table("creator_languages")
    op.drop_table("creator_categories")
    op.drop_table("creator_username_history")
    op.drop_table("creator_status_history")
    op.drop_table("creator_verifications")
    op.drop_index("uq_creator_profiles_username_lower", table_name="creator_profiles")
    op.drop_table("creator_profiles")
    op.execute("DROP TYPE verification_status")
    op.execute("DROP TYPE creator_status")
