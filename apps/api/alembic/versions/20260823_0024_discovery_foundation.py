"""Create bounded, derived discovery controls and analytics events.

Revision ID: 20260823_0024
Revises: 20260822_0023
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260823_0024"
down_revision = "20260822_0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    entity = postgresql.ENUM(
        "creator",
        "post",
        "video",
        "gallery",
        "marketplace_listing",
        "live_room",
        name="discovery_entity_type",
        create_type=False,
    )
    postgresql.ENUM(
        "creator",
        "post",
        "video",
        "gallery",
        "marketplace_listing",
        "live_room",
        name="discovery_entity_type",
    ).create(op.get_bind(), checkfirst=True)
    op.create_table(
        "discovery_configs",
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
        sa.Column("version", sa.Integer(), nullable=False, unique=True),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("text_weight", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("live_boost", sa.Integer(), nullable=False, server_default="40"),
        sa.Column("recency_weight", sa.Integer(), nullable=False, server_default="20"),
        sa.Column("engagement_weight", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("trending_window_hours", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("default_result_limit", sa.Integer(), nullable=False, server_default="20"),
    )
    op.create_index("ix_discovery_configs_version", "discovery_configs", ["version"])
    op.create_index("ix_discovery_configs_is_current", "discovery_configs", ["is_current"])
    op.create_table(
        "discovery_hides",
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
        sa.Column("entity_type", entity, nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.UniqueConstraint("entity_type", "entity_id", name="uq_discovery_hide_entity"),
    )
    op.create_index("ix_discovery_hides_entity_type", "discovery_hides", ["entity_type"])
    op.create_index("ix_discovery_hides_entity_id", "discovery_hides", ["entity_id"])
    op.create_table(
        "discovery_events",
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
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("request_key", sa.String(length=128), nullable=False),
        sa.Column(
            "actor_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("entity_type", entity),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True)),
        sa.Column("ranking_version", sa.Integer(), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.UniqueConstraint(
            "event_type",
            "request_key",
            "entity_type",
            "entity_id",
            name="uq_discovery_event_dedupe",
        ),
    )
    op.create_index("ix_discovery_events_event_type", "discovery_events", ["event_type"])
    op.create_index("ix_discovery_events_request_key", "discovery_events", ["request_key"])
    op.create_index("ix_discovery_events_actor_user_id", "discovery_events", ["actor_user_id"])
    op.create_index("ix_discovery_events_entity_id", "discovery_events", ["entity_id"])
    op.execute(
        "CREATE INDEX ix_creator_profiles_discovery_text ON creator_profiles USING gin (to_tsvector('simple', coalesce(username, '') || ' ' || coalesce(display_name, '') || ' ' || coalesce(bio, '')))"
    )
    op.execute(
        "CREATE INDEX ix_marketplace_listings_discovery_text ON marketplace_listings USING gin (to_tsvector('simple', title || ' ' || coalesce(description, '') || ' ' || category))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_marketplace_listings_discovery_text")
    op.execute("DROP INDEX IF EXISTS ix_creator_profiles_discovery_text")
    op.drop_table("discovery_events")
    op.drop_table("discovery_hides")
    op.drop_table("discovery_configs")
    sa.Enum(name="discovery_entity_type").drop(op.get_bind(), checkfirst=True)
