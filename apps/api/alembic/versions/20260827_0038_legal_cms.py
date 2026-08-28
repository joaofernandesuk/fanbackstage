"""Add versioned legal CMS, exact acceptances, and site settings."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260827_0038"
down_revision = "20260827_0037"
branch_labels = None
depends_on = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


def upgrade() -> None:
    document_type = postgresql.ENUM(
        "terms",
        "privacy",
        "cookies",
        "community_guidelines",
        "acceptable_use",
        "prohibited_content",
        "creator_agreement",
        "fan_terms",
        "refund_policy",
        "marketplace_terms",
        "live_rules",
        "age_policy",
        "copyright",
        "complaints",
        "appeals",
        "performer_consent",
        "contact_support",
        "record_keeping_notice",
        name="legal_document_type",
        create_type=False,
    )
    audience = postgresql.ENUM(
        "all_users",
        "fan",
        "creator",
        "group_manager",
        "affiliate",
        name="legal_audience",
        create_type=False,
    )
    document_status = postgresql.ENUM(
        "draft",
        "published",
        "retired",
        name="legal_document_status",
        create_type=False,
    )
    bind = op.get_bind()
    for enum in (document_type, audience, document_status):
        enum.create(bind, checkfirst=True)

    op.create_table(
        "legal_documents",
        sa.Column("id", sa.UUID(), primary_key=True),
        *_timestamps(),
        sa.Column("document_type", document_type, nullable=False),
        sa.Column("slug", sa.String(96), nullable=False),
        sa.Column(
            "jurisdiction_code",
            sa.String(2),
            sa.ForeignKey("country_registry.code", ondelete="RESTRICT"),
        ),
        sa.Column("language", sa.String(16), nullable=False),
        sa.Column("audience", audience, nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jurisdiction_code IS NULL OR jurisdiction_code ~ '^[A-Z]{2}$'",
            name="ck_legal_documents_jurisdiction_iso_alpha2",
        ),
    )
    for column in (
        "document_type",
        "slug",
        "jurisdiction_code",
        "language",
        "audience",
        "created_by_user_id",
    ):
        op.create_index(f"ix_legal_documents_{column}", "legal_documents", [column])
    op.create_index(
        "uq_legal_documents_global_slug_language_audience",
        "legal_documents",
        ["slug", "language", "audience"],
        unique=True,
        postgresql_where=sa.text("jurisdiction_code IS NULL"),
    )
    op.create_index(
        "uq_legal_documents_country_slug_language_audience",
        "legal_documents",
        ["slug", "jurisdiction_code", "language", "audience"],
        unique=True,
        postgresql_where=sa.text("jurisdiction_code IS NOT NULL"),
    )

    op.create_table(
        "legal_document_versions",
        sa.Column("id", sa.UUID(), primary_key=True),
        *_timestamps(),
        sa.Column(
            "document_id",
            sa.UUID(),
            sa.ForeignKey("legal_documents.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            document_status,
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column(
            "body_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("effective_from", sa.DateTime(timezone=True)),
        sa.Column("effective_until", sa.DateTime(timezone=True)),
        sa.Column("requires_acceptance", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("requires_legal_review", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "approved_for_publication",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("is_demo", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column(
            "published_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("retired_at", sa.DateTime(timezone=True)),
        sa.Column(
            "retired_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.UniqueConstraint("document_id", "version", name="uq_legal_document_version_number"),
        sa.CheckConstraint("version > 0", name="ck_legal_document_versions_positive_version"),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_from IS NULL OR effective_until > effective_from",
            name="ck_legal_document_versions_effective_window",
        ),
    )
    for column in (
        "document_id",
        "status",
        "effective_from",
        "effective_until",
        "requires_acceptance",
        "is_demo",
        "created_by_user_id",
        "published_at",
        "published_by_user_id",
        "retired_at",
        "retired_by_user_id",
    ):
        op.create_index(
            f"ix_legal_document_versions_{column}",
            "legal_document_versions",
            [column],
        )

    op.create_table(
        "legal_acceptances",
        sa.Column("id", sa.UUID(), primary_key=True),
        *_timestamps(),
        sa.Column(
            "user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "document_version_id",
            sa.UUID(),
            sa.ForeignKey("legal_document_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column(
            "jurisdiction_code",
            sa.String(2),
            sa.ForeignKey("country_registry.code", ondelete="RESTRICT"),
        ),
        sa.Column("correlation_id", sa.String(80)),
        sa.UniqueConstraint(
            "user_id",
            "document_version_id",
            name="uq_legal_acceptance_user_version",
        ),
        sa.CheckConstraint(
            "jurisdiction_code IS NULL OR jurisdiction_code ~ '^[A-Z]{2}$'",
            name="ck_legal_acceptances_jurisdiction_iso_alpha2",
        ),
    )
    for column in (
        "user_id",
        "document_version_id",
        "accepted_at",
        "jurisdiction_code",
        "correlation_id",
    ):
        op.create_index(f"ix_legal_acceptances_{column}", "legal_acceptances", [column])

    op.create_table(
        "site_settings_versions",
        sa.Column("id", sa.UUID(), primary_key=True),
        *_timestamps(),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("support_email", sa.String(320)),
        sa.Column("footer_text", sa.String(500)),
        sa.Column("public_contact_text", sa.String(1000)),
        sa.Column(
            "social_links_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("homepage_announcement", sa.Text()),
        sa.Column("maintenance_notice", sa.Text()),
        sa.Column("banner_level", sa.String(24), nullable=False, server_default=sa.text("'info'")),
        sa.Column("banner_starts_at", sa.DateTime(timezone=True)),
        sa.Column("banner_ends_at", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_by_user_id",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(500), nullable=False),
        sa.UniqueConstraint("version", name="uq_site_settings_version"),
        sa.CheckConstraint("version > 0", name="ck_site_settings_positive_version"),
        sa.CheckConstraint(
            "banner_ends_at IS NULL OR banner_starts_at IS NULL OR "
            "banner_ends_at > banner_starts_at",
            name="ck_site_settings_banner_window",
        ),
    )
    for column in (
        "is_current",
        "banner_starts_at",
        "banner_ends_at",
        "updated_by_user_id",
    ):
        op.create_index(f"ix_site_settings_versions_{column}", "site_settings_versions", [column])
    op.create_index(
        "uq_site_settings_one_current",
        "site_settings_versions",
        ["is_current"],
        unique=True,
        postgresql_where=sa.text("is_current IS TRUE"),
    )

    op.execute(
        """
        CREATE FUNCTION enforce_legal_document_immutability()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'legal document identity and scope are immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_legal_document_immutability
        BEFORE UPDATE OR DELETE ON legal_documents
        FOR EACH ROW EXECUTE FUNCTION enforce_legal_document_immutability()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_legal_document_version_immutability()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'draft'::legal_document_status THEN
                    RAISE EXCEPTION 'published or retired legal document versions are immutable';
                END IF;
                RETURN OLD;
            END IF;
            IF OLD.status = 'draft'::legal_document_status THEN
                RETURN NEW;
            END IF;
            IF OLD.status = 'published'::legal_document_status
               AND NEW.status = 'retired'::legal_document_status
               AND NEW.retired_at IS NOT NULL
               AND NEW.retired_by_user_id IS NOT NULL
               AND ROW(
                    NEW.id, NEW.created_at, NEW.document_id, NEW.version,
                    NEW.title, NEW.body_json, NEW.effective_from, NEW.effective_until,
                    NEW.requires_acceptance, NEW.requires_legal_review,
                    NEW.approved_for_publication, NEW.is_demo, NEW.created_by_user_id,
                    NEW.published_at, NEW.published_by_user_id
               ) IS NOT DISTINCT FROM ROW(
                    OLD.id, OLD.created_at, OLD.document_id, OLD.version,
                    OLD.title, OLD.body_json, OLD.effective_from, OLD.effective_until,
                    OLD.requires_acceptance, OLD.requires_legal_review,
                    OLD.approved_for_publication, OLD.is_demo, OLD.created_by_user_id,
                    OLD.published_at, OLD.published_by_user_id
               ) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'published or retired legal document versions are immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_legal_document_version_immutability
        BEFORE UPDATE OR DELETE ON legal_document_versions
        FOR EACH ROW EXECUTE FUNCTION enforce_legal_document_version_immutability()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_legal_acceptance_immutability()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'legal acceptance evidence is immutable';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_legal_acceptance_immutability
        BEFORE UPDATE OR DELETE ON legal_acceptances
        FOR EACH ROW EXECUTE FUNCTION enforce_legal_acceptance_immutability()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_site_settings_history_immutability()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND OLD.is_current IS TRUE
               AND NEW.is_current IS FALSE
               AND ROW(
                    NEW.id, NEW.created_at, NEW.version, NEW.support_email,
                    NEW.footer_text, NEW.public_contact_text, NEW.social_links_json,
                    NEW.homepage_announcement, NEW.maintenance_notice,
                    NEW.banner_level, NEW.banner_starts_at, NEW.banner_ends_at,
                    NEW.updated_by_user_id, NEW.reason
               ) IS NOT DISTINCT FROM ROW(
                    OLD.id, OLD.created_at, OLD.version, OLD.support_email,
                    OLD.footer_text, OLD.public_contact_text, OLD.social_links_json,
                    OLD.homepage_announcement, OLD.maintenance_notice,
                    OLD.banner_level, OLD.banner_starts_at, OLD.banner_ends_at,
                    OLD.updated_by_user_id, OLD.reason
               ) THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'site settings history is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_site_settings_history_immutability
        BEFORE UPDATE OR DELETE ON site_settings_versions
        FOR EACH ROW EXECUTE FUNCTION enforce_site_settings_history_immutability()
        """
    )
    op.execute(
        """
        CREATE FUNCTION enforce_audit_event_immutability()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'UPDATE'
               AND OLD.actor_user_id IS NOT NULL
               AND NEW.actor_user_id IS NULL
               AND ROW(
                    NEW.id, NEW.created_at, NEW.updated_at, NEW.event_type, NEW.target_type,
                    NEW.target_id, NEW.correlation_id, NEW.ip_address,
                    NEW.user_agent, NEW.metadata_json
               ) IS NOT DISTINCT FROM ROW(
                    OLD.id, OLD.created_at, OLD.updated_at, OLD.event_type, OLD.target_type,
                    OLD.target_id, OLD.correlation_id, OLD.ip_address,
                    OLD.user_agent, OLD.metadata_json
               ) THEN
                -- Preserve the existing users.actor FK's privacy-erasure path;
                -- every substantive audit field remains immutable.
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'audit event history is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_event_immutability
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW EXECUTE FUNCTION enforce_audit_event_immutability()
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    retained = {
        table: bool(bind.scalar(sa.text(f"SELECT EXISTS (SELECT 1 FROM {table} LIMIT 1)")))
        for table in (
            "legal_acceptances",
            "legal_document_versions",
            "legal_documents",
            "site_settings_versions",
        )
    }
    if any(retained.values()):
        tables = ", ".join(table for table, has_rows in retained.items() if has_rows)
        raise RuntimeError(
            "Refusing to downgrade 20260827_0038 because retained legal/site "
            f"history exists in: {tables}"
        )
    op.execute("DROP TRIGGER IF EXISTS trg_audit_event_immutability ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS enforce_audit_event_immutability()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_site_settings_history_immutability ON site_settings_versions"
    )
    op.execute("DROP FUNCTION IF EXISTS enforce_site_settings_history_immutability()")
    op.execute("DROP TRIGGER IF EXISTS trg_legal_acceptance_immutability ON legal_acceptances")
    op.execute("DROP FUNCTION IF EXISTS enforce_legal_acceptance_immutability()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_legal_document_version_immutability ON legal_document_versions"
    )
    op.execute("DROP FUNCTION IF EXISTS enforce_legal_document_version_immutability()")
    op.execute("DROP TRIGGER IF EXISTS trg_legal_document_immutability ON legal_documents")
    op.execute("DROP FUNCTION IF EXISTS enforce_legal_document_immutability()")
    op.drop_table("site_settings_versions")
    op.drop_table("legal_acceptances")
    op.drop_table("legal_document_versions")
    op.drop_table("legal_documents")
    for name in ("legal_document_status", "legal_audience", "legal_document_type"):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
