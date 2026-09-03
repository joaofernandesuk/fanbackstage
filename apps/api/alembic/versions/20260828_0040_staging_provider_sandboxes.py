"""Add staging-only payment and creator-KYC simulator delivery records."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260828_0040"
down_revision = "20260831_0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    status = postgresql.ENUM("pending", "delivered", name="sandbox_event_status", create_type=False)
    status.create(bind, checkfirst=True)
    op.create_table(
        "staging_payment_sandbox_events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("payment_attempt_id", sa.UUID(), sa.ForeignKey("payment_attempts.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("external_event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("deliver_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", status, server_default="pending", nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("external_event_id", name="uq_staging_payment_sandbox_event_id"),
        sa.UniqueConstraint("payment_attempt_id", "event_type", name="uq_staging_payment_event_type"),
    )
    op.create_index("ix_staging_payment_sandbox_events_payment_attempt_id", "staging_payment_sandbox_events", ["payment_attempt_id"])
    op.create_index("ix_staging_payment_sandbox_events_deliver_after", "staging_payment_sandbox_events", ["deliver_after"])
    op.create_index("ix_staging_payment_sandbox_events_status", "staging_payment_sandbox_events", ["status"])
    op.create_table(
        "creator_kyc_webhook_events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("external_event_id", sa.String(255), nullable=False),
        sa.Column("creator_verification_id", sa.UUID(), sa.ForeignKey("creator_verifications.id", ondelete="SET NULL")),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("provider", "external_event_id", name="uq_creator_kyc_webhook_event"),
    )
    op.create_index("ix_creator_kyc_webhook_events_creator_verification_id", "creator_kyc_webhook_events", ["creator_verification_id"])
    op.create_table(
        "staging_creator_kyc_sandbox_events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("creator_verification_id", sa.UUID(), sa.ForeignKey("creator_verifications.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("external_event_id", sa.String(255), nullable=False),
        sa.Column("outcome", sa.String(64), nullable=False),
        sa.Column("deliver_after", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("external_event_id", name="uq_staging_creator_kyc_event_id"),
        sa.UniqueConstraint("creator_verification_id", name="uq_staging_creator_kyc_event_verification"),
    )
    op.create_index("ix_staging_creator_kyc_sandbox_events_creator_verification_id", "staging_creator_kyc_sandbox_events", ["creator_verification_id"])
    op.create_index("ix_staging_creator_kyc_sandbox_events_deliver_after", "staging_creator_kyc_sandbox_events", ["deliver_after"])


def downgrade() -> None:
    op.drop_table("staging_creator_kyc_sandbox_events")
    op.drop_table("creator_kyc_webhook_events")
    op.drop_table("staging_payment_sandbox_events")
    postgresql.ENUM(name="sandbox_event_status").drop(op.get_bind(), checkfirst=True)
