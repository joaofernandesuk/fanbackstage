"""Add Phase 6 messaging foundation and message unlock commerce."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260821_0010"
down_revision = "20260820_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE TYPE messaging_permission AS ENUM ('anyone', 'followers', 'subscribers', 'previous_customers', 'nobody')"
    )
    op.execute("CREATE TYPE message_type AS ENUM ('text', 'media', 'content_reference', 'system')")
    op.execute("CREATE TYPE message_status AS ENUM ('sent', 'removed')")
    op.execute(
        "CREATE TYPE message_audience_segment AS ENUM ('followers', 'active_subscribers', 'expired_subscribers', 'previous_customers')"
    )
    op.execute(
        "CREATE TYPE mass_message_campaign_status AS ENUM ('draft', 'scheduled', 'processing', 'completed', 'cancelled')"
    )
    op.execute("ALTER TYPE ledger_transaction_type ADD VALUE IF NOT EXISTS 'messaging_charge'")
    op.create_table(
        "conversations",
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
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "viewer_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("last_message_at", sa.DateTime(timezone=True)),
        sa.Column("archived_by_creator", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("archived_by_viewer", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("muted_by_creator", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("muted_by_viewer", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.UniqueConstraint("creator_id", "viewer_user_id", name="uq_conversation_creator_viewer"),
    )
    op.create_index(
        "ix_conversation_creator_last_message", "conversations", ["creator_id", "last_message_at"]
    )
    op.create_table(
        "conversation_participants",
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
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
        ),
        sa.Column("last_read_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("conversation_id", "user_id", name="uq_conversation_participant"),
    )
    op.create_index(
        "ix_conversation_participants_conversation_id",
        "conversation_participants",
        ["conversation_id"],
    )
    op.create_table(
        "creator_messaging_settings",
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
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creator_profiles.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "permission",
            postgresql.ENUM(name="messaging_permission", create_type=False),
            nullable=False,
            server_default="anyone",
        ),
        sa.Column("send_fee_minor", sa.Integer()),
        sa.Column("send_fee_currency", sa.String(3)),
        sa.Column("subscribers_free", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.CheckConstraint(
            "send_fee_minor IS NULL OR send_fee_minor > 0", name="ck_messaging_send_fee_positive"
        ),
    )
    op.create_table(
        "messages",
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
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("conversations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "sender_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "reply_to_message_id", sa.Uuid(), sa.ForeignKey("messages.id", ondelete="RESTRICT")
        ),
        sa.Column(
            "message_type",
            postgresql.ENUM(name="message_type", create_type=False),
            nullable=False,
            server_default="text",
        ),
        sa.Column("body", sa.Text()),
        sa.Column(
            "status",
            postgresql.ENUM(name="message_status", create_type=False),
            nullable=False,
            server_default="sent",
        ),
        sa.Column("removed_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_message_conversation_created", "messages", ["conversation_id", "created_at", "id"]
    )
    op.create_table(
        "user_blocks",
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
        sa.Column(
            "blocker_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "blocked_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("blocker_user_id", "blocked_user_id", name="uq_user_block"),
        sa.CheckConstraint("blocker_user_id <> blocked_user_id", name="ck_user_block_not_self"),
    )
    op.create_table(
        "message_attachments",
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
        sa.Column(
            "message_id",
            sa.Uuid(),
            sa.ForeignKey("messages.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "media_asset_id",
            sa.Uuid(),
            sa.ForeignKey("media_assets.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("unlock_price_minor", sa.Integer()),
        sa.Column("unlock_currency", sa.String(3)),
        sa.UniqueConstraint("message_id", "media_asset_id", name="uq_message_attachment_asset"),
        sa.CheckConstraint(
            "unlock_price_minor IS NULL OR unlock_price_minor > 0",
            name="ck_message_attachment_price",
        ),
        sa.CheckConstraint(
            "(unlock_price_minor IS NULL AND unlock_currency IS NULL) OR (unlock_price_minor IS NOT NULL AND unlock_currency IS NOT NULL)",
            name="ck_message_attachment_currency",
        ),
    )
    op.create_table(
        "message_unlock_purchases",
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
        sa.Column(
            "buyer_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "seller_creator_id",
            sa.Uuid(),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "message_attachment_id",
            sa.Uuid(),
            sa.ForeignKey("message_attachments.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "payment_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("payment_attempts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("gross_amount_minor", sa.Integer(), nullable=False),
        sa.Column("platform_fee_minor", sa.Integer(), nullable=False),
        sa.Column("creator_amount_minor", sa.Integer(), nullable=False),
        sa.Column("commission_basis_points", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="awaiting_payment"),
        sa.Column(
            "ledger_transaction_id",
            sa.Uuid(),
            sa.ForeignKey("ledger_transactions.id", ondelete="RESTRICT"),
        ),
        sa.Column("purchased_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "buyer_user_id", "message_attachment_id", name="uq_message_unlock_buyer_attachment"
        ),
        sa.UniqueConstraint("payment_attempt_id", name="uq_message_unlock_payment_attempt"),
        sa.UniqueConstraint("ledger_transaction_id", name="uq_message_unlock_ledger"),
        sa.CheckConstraint(
            "gross_amount_minor = platform_fee_minor + creator_amount_minor",
            name="ck_message_unlock_balance",
        ),
    )
    op.create_table(
        "pending_message_sends",
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
        sa.Column(
            "buyer_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "payment_attempt_id",
            sa.Uuid(),
            sa.ForeignKey("payment_attempts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("gross_amount_minor", sa.Integer(), nullable=False),
        sa.Column("platform_fee_minor", sa.Integer(), nullable=False),
        sa.Column("creator_amount_minor", sa.Integer(), nullable=False),
        sa.Column("commission_basis_points", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="awaiting_payment"),
        sa.Column(
            "ledger_transaction_id",
            sa.Uuid(),
            sa.ForeignKey("ledger_transactions.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.Column(
            "message_id", sa.Uuid(), sa.ForeignKey("messages.id", ondelete="RESTRICT"), unique=True
        ),
        sa.UniqueConstraint("payment_attempt_id", name="uq_pending_message_send_payment"),
        sa.UniqueConstraint("message_id", name="uq_pending_message_send_message"),
        sa.CheckConstraint(
            "gross_amount_minor = platform_fee_minor + creator_amount_minor",
            name="ck_pending_message_send_balance",
        ),
    )
    op.create_table(
        "mass_message_campaigns",
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
        sa.Column(
            "creator_id",
            sa.Uuid(),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "audience_segment",
            postgresql.ENUM(name="message_audience_segment", create_type=False),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(name="mass_message_campaign_status", create_type=False),
            nullable=False,
            server_default="draft",
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_mass_campaign_due", "mass_message_campaigns", ["status", "scheduled_at"])
    op.create_table(
        "mass_message_recipients",
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
        sa.Column(
            "campaign_id",
            sa.Uuid(),
            sa.ForeignKey("mass_message_campaigns.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "recipient_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "message_id", sa.Uuid(), sa.ForeignKey("messages.id", ondelete="RESTRICT"), unique=True
        ),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("campaign_id", "recipient_user_id", name="uq_mass_campaign_recipient"),
    )


def downgrade() -> None:
    for table in (
        "mass_message_recipients",
        "mass_message_campaigns",
        "pending_message_sends",
        "message_unlock_purchases",
        "message_attachments",
        "user_blocks",
        "messages",
        "creator_messaging_settings",
        "conversation_participants",
        "conversations",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    for name in (
        "mass_message_campaign_status",
        "message_audience_segment",
        "message_status",
        "message_type",
        "messaging_permission",
    ):
        op.execute(f"DROP TYPE {name}")
    # PostgreSQL enum values are intentionally not removed; a production rollback uses a forward correction.
