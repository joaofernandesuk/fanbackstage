"""Add adult self-attestation and fail-closed media audience classification."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260826_0034"
down_revision = "20260826_0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("adult_attested_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("adult_attestation_version", sa.String(64)))
    op.create_check_constraint(
        "ck_users_adult_attestation_complete",
        "users",
        "(adult_attested_at IS NULL AND adult_attestation_version IS NULL) OR "
        "(adult_attested_at IS NOT NULL AND adult_attestation_version IS NOT NULL)",
    )

    media_audience = postgresql.ENUM(
        "safe_public", "adult_restricted", name="media_audience", create_type=False
    )
    media_audience.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "media_assets",
        sa.Column(
            "audience",
            media_audience,
            nullable=False,
            server_default=sa.text("'adult_restricted'"),
        ),
    )
    op.create_index("ix_media_assets_audience", "media_assets", ["audience"])


def downgrade() -> None:
    op.drop_index("ix_media_assets_audience", table_name="media_assets")
    op.drop_column("media_assets", "audience")
    postgresql.ENUM(name="media_audience").drop(op.get_bind(), checkfirst=True)
    op.drop_constraint("ck_users_adult_attestation_complete", "users", type_="check")
    op.drop_column("users", "adult_attestation_version")
    op.drop_column("users", "adult_attested_at")
