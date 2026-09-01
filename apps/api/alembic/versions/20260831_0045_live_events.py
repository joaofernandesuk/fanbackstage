"""Add the canonical replay-safe Live activity event projection."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260831_0045"
down_revision = "20260827_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "live_events",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("live_room_id", sa.UUID(), sa.ForeignKey("live_rooms.id", ondelete="RESTRICT")),
        sa.Column(
            "private_session_id",
            sa.UUID(),
            sa.ForeignKey("private_sessions.id", ondelete="RESTRICT"),
        ),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_user_id", sa.UUID(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column(
            "ledger_transaction_id",
            sa.UUID(),
            sa.ForeignKey("ledger_transactions.id", ondelete="RESTRICT"),
            unique=True,
        ),
        sa.Column("source_type", sa.String(80)),
        sa.Column("source_id", sa.String(255)),
        sa.Column("amount_minor", sa.Integer()),
        sa.Column("currency", sa.String(3)),
        sa.Column("presentation_hidden", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.CheckConstraint(
            "(live_room_id IS NOT NULL) OR (private_session_id IS NOT NULL)",
            name="ck_live_event_context",
        ),
        sa.CheckConstraint("btrim(event_type) <> ''", name="ck_live_event_type"),
        sa.CheckConstraint(
            "amount_minor IS NULL OR amount_minor > 0", name="ck_live_event_positive_amount"
        ),
        sa.UniqueConstraint("idempotency_key", name="uq_live_events_idempotency"),
    )
    op.create_index("ix_live_events_live_room_id", "live_events", ["live_room_id"])
    op.create_index("ix_live_events_private_session_id", "live_events", ["private_session_id"])
    op.create_index("ix_live_events_actor_user_id", "live_events", ["actor_user_id"])
    op.create_index("ix_live_events_occurred_at", "live_events", ["occurred_at"])
    op.create_index(
        "ix_live_events_room_occurred", "live_events", ["live_room_id", "occurred_at", "id"]
    )
    op.create_index(
        "ix_live_events_session_occurred",
        "live_events",
        ["private_session_id", "occurred_at", "id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT EXISTS (SELECT 1 FROM live_events LIMIT 1)")):
        raise RuntimeError(
            "Refusing to downgrade 20260831_0045 because Live activity history exists"
        )
    op.drop_table("live_events")
