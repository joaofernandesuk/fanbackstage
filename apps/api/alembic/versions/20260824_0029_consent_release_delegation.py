"""Add explicit delegated consent-release management permission.

This intentional forward Phase 13 migration follows 0028. Consent evidence and
verification remain Trust & Safety-owned; this value grants only creator-scoped
submission/revocation management to an explicitly delegated group manager.
"""

from alembic import op

revision = "20260824_0029"
down_revision = "20260824_0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE group_permission ADD VALUE IF NOT EXISTS 'manage_consent_releases'")


def downgrade() -> None:
    op.execute("ALTER TABLE group_permission_grants ALTER COLUMN permission DROP DEFAULT")
    op.execute(
        "ALTER TABLE group_permission_grants ALTER COLUMN permission TYPE text USING permission::text"
    )
    op.execute("ALTER TYPE group_permission RENAME TO group_permission_phase13_old")
    op.execute(
        "CREATE TYPE group_permission AS ENUM "
        "('view_profile', 'edit_profile', 'manage_content', 'publish_posts', "
        "'manage_subscriptions', 'manage_promotions', 'manage_messaging', "
        "'manage_live_settings', 'view_analytics', 'view_earnings', "
        "'manage_marketplace', 'manage_marketplace_orders', 'manage_featuring')"
    )
    op.execute(
        "ALTER TABLE group_permission_grants ALTER COLUMN permission TYPE group_permission "
        "USING permission::group_permission"
    )
    op.execute("DROP TYPE group_permission_phase13_old")
