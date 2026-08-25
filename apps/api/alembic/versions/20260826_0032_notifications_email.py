"""Add durable notification intents, preferences, delivery attempts and suppressions."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260826_0032"
down_revision = "20260825_0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    notification_class = postgresql.ENUM(
        "transactional", "marketing", name="notification_class", create_type=False
    )
    notification_priority = postgresql.ENUM(
        "critical_security",
        "transactional",
        "normal",
        "marketing",
        name="notification_priority",
        create_type=False,
    )
    notification_channel = postgresql.ENUM(
        "email", "in_app", name="notification_channel", create_type=False
    )
    delivery_status = postgresql.ENUM(
        "queued",
        "processing",
        "sent",
        "delivered",
        "failed_retryable",
        "failed_permanent",
        "suppressed",
        name="delivery_status",
        create_type=False,
    )
    suppression_reason = postgresql.ENUM(
        "hard_bounce",
        "complaint",
        "manual",
        "marketing_unsubscribe",
        name="suppression_reason",
        create_type=False,
    )
    bind = op.get_bind()
    for enum in (
        notification_class,
        notification_priority,
        notification_channel,
        delivery_status,
        suppression_reason,
    ):
        enum.create(bind, checkfirst=True)
    op.create_table(
        "notification_intents",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "recipient_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("notification_type", sa.String(96), nullable=False),
        sa.Column("classification", notification_class, nullable=False),
        sa.Column("priority", notification_priority, nullable=False),
        sa.Column("source_domain", sa.String(64), nullable=False),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("idempotency_key", sa.String(192), nullable=False, unique=True),
        sa.Column("payload_json", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("secure_payload", sa.Text()),
    )
    op.create_index(
        "ix_notification_intents_recipient_user_id", "notification_intents", ["recipient_user_id"]
    )
    op.create_index(
        "ix_notification_intents_notification_type", "notification_intents", ["notification_type"]
    )
    op.create_table(
        "in_app_notifications",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "intent_id",
            sa.UUID(),
            sa.ForeignKey("notification_intents.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "recipient_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("notification_type", sa.String(96), nullable=False),
        sa.Column("title", sa.String(180), nullable=False),
        sa.Column("body", sa.String(500), nullable=False),
        sa.Column("target_path", sa.String(512)),
        sa.Column("read_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_in_app_notifications_recipient_user_id",
        "in_app_notifications",
        ["recipient_user_id", "created_at"],
    )
    op.create_index(
        "ix_in_app_notifications_unread", "in_app_notifications", ["recipient_user_id", "read_at"]
    )
    op.create_table(
        "notification_preferences",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("category", sa.String(48), nullable=False),
        sa.Column("email_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("in_app_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("consented_at", sa.DateTime(timezone=True)),
        sa.Column("consent_source", sa.String(96)),
        sa.UniqueConstraint("user_id", "category"),
    )
    op.create_index("ix_notification_preferences_user_id", "notification_preferences", ["user_id"])
    op.create_table(
        "email_suppressions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("email_hash", sa.String(64), nullable=False),
        sa.Column("reason", suppression_reason, nullable=False),
        sa.Column("marketing_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("source", sa.String(96), nullable=False),
        sa.UniqueConstraint("email_hash", "reason"),
    )
    op.create_index("ix_email_suppressions_email_hash", "email_suppressions", ["email_hash"])
    op.create_table(
        "notification_delivery_attempts",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "intent_id",
            sa.UUID(),
            sa.ForeignKey("notification_intents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel", notification_channel, nullable=False),
        sa.Column("status", delivery_status, nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_message_id", sa.String(255), unique=True),
        sa.Column("recipient_snapshot", sa.String(320)),
        sa.Column("template_key", sa.String(96)),
        sa.Column("template_version", sa.Integer()),
        sa.Column("error_code", sa.String(96)),
    )
    op.create_index(
        "ix_notification_delivery_attempts_intent_id",
        "notification_delivery_attempts",
        ["intent_id"],
    )
    op.create_index(
        "ix_notification_delivery_attempts_status", "notification_delivery_attempts", ["status"]
    )
    op.create_index(
        "ix_notification_delivery_attempts_provider_message_id",
        "notification_delivery_attempts",
        ["provider_message_id"],
    )


def downgrade() -> None:
    for table in (
        "notification_delivery_attempts",
        "email_suppressions",
        "notification_preferences",
        "in_app_notifications",
        "notification_intents",
    ):
        op.drop_table(table)
    bind = op.get_bind()
    for name in (
        "suppression_reason",
        "delivery_status",
        "notification_channel",
        "notification_priority",
        "notification_class",
    ):
        sa.Enum(name=name).drop(bind, checkfirst=True)
