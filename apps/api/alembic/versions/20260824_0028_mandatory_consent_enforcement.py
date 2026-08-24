"""Require a forward content policy marker for Phase 13 consent enforcement.

This forward migration follows the already-published case-management foundation
in 20260823_0027. It adds only the server-owned content policy marker needed to
fail closed when Trust & Safety determines identifiable additional participants
require a verified, scoped release.

Revision ID: 20260824_0028
Revises: 20260823_0027
"""

import sqlalchemy as sa

from alembic import op

revision = "20260824_0028"
down_revision = "20260823_0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "content_items",
        sa.Column(
            "requires_verified_consent", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.alter_column("content_items", "requires_verified_consent", server_default=None)


def downgrade() -> None:
    op.drop_column("content_items", "requires_verified_consent")
