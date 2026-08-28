"""Add a durable outbox for replay-safe LiveKit control commands."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260827_0039"
down_revision = "20260827_0038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    action = postgresql.ENUM(
        "delete_room",
        "remove_participant",
        name="live_provider_control_action",
        create_type=False,
    )
    status = postgresql.ENUM(
        "pending",
        "processing",
        "succeeded",
        "failed_terminal",
        name="live_provider_control_status",
        create_type=False,
    )
    bind = op.get_bind()
    action.create(bind, checkfirst=True)
    status.create(bind, checkfirst=True)

    op.create_table(
        "live_provider_control_intents",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("action", action, nullable=False),
        sa.Column("target_type", sa.String(80), nullable=False),
        sa.Column("target_id", sa.String(255), nullable=False),
        sa.Column("provider_room_name", sa.String(128), nullable=False),
        sa.Column("participant_identity", sa.String(255)),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
        ),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("status", status, server_default="pending", nullable=False),
        sa.Column("retryable", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "next_attempt_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column("succeeded_at", sa.DateTime(timezone=True)),
        sa.Column("terminal_failed_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(96)),
        sa.Column("last_error_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "(action = 'delete_room' AND participant_identity IS NULL) OR "
            "(action = 'remove_participant' AND "
            "participant_identity IS NOT NULL AND btrim(participant_identity) <> '')",
            name="ck_live_provider_control_action_target",
        ),
        sa.CheckConstraint(
            "btrim(target_type) <> '' AND btrim(target_id) <> '' "
            "AND btrim(provider_room_name) <> '' AND btrim(reason) <> '' "
            "AND btrim(idempotency_key) <> ''",
            name="ck_live_provider_control_required_text",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="ck_live_provider_control_attempt_count",
        ),
        sa.CheckConstraint(
            "(last_error_code IS NULL AND last_error_at IS NULL) OR "
            "(last_error_code IS NOT NULL AND last_error_at IS NOT NULL)",
            name="ck_live_provider_control_error_pair",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND retryable IS TRUE "
            "AND next_attempt_at IS NOT NULL AND lease_expires_at IS NULL "
            "AND succeeded_at IS NULL AND terminal_failed_at IS NULL) OR "
            "(status = 'processing' AND retryable IS TRUE "
            "AND next_attempt_at IS NULL AND lease_expires_at IS NOT NULL "
            "AND last_attempt_at IS NOT NULL AND attempt_count > 0 "
            "AND succeeded_at IS NULL AND terminal_failed_at IS NULL) OR "
            "(status = 'succeeded' AND retryable IS FALSE "
            "AND next_attempt_at IS NULL AND lease_expires_at IS NULL "
            "AND succeeded_at IS NOT NULL AND terminal_failed_at IS NULL) OR "
            "(status = 'failed_terminal' AND retryable IS FALSE "
            "AND next_attempt_at IS NULL AND lease_expires_at IS NULL "
            "AND succeeded_at IS NULL AND terminal_failed_at IS NOT NULL "
            "AND last_error_code IS NOT NULL AND last_error_at IS NOT NULL)",
            name="ck_live_provider_control_status_state",
        ),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_live_provider_control_intents_idempotency_key",
        ),
    )
    op.create_index(
        "ix_live_provider_control_intents_actor_user_id",
        "live_provider_control_intents",
        ["actor_user_id"],
    )
    op.create_index(
        "ix_live_provider_control_intents_status",
        "live_provider_control_intents",
        ["status"],
    )
    op.create_index(
        "ix_live_provider_control_due",
        "live_provider_control_intents",
        ["status", "next_attempt_at", "lease_expires_at", "created_at", "id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(
        sa.text("SELECT EXISTS (SELECT 1 FROM live_provider_control_intents LIMIT 1)")
    ):
        raise RuntimeError(
            "Refusing to downgrade 20260827_0039 because durable LiveKit "
            "provider-control intents exist"
        )
    op.drop_table("live_provider_control_intents")
    postgresql.ENUM(name="live_provider_control_status").drop(bind, checkfirst=True)
    postgresql.ENUM(name="live_provider_control_action").drop(bind, checkfirst=True)
