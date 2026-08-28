"""Add versioned jurisdiction policy and durable age-assurance foundations.

Revision ID: 20260827_0037
Revises: 20260826_0036
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "20260827_0037"
down_revision = "20260826_0036"
branch_labels = None
depends_on = None


# ISO identifiers and ordinary English display names only. This registry makes
# no legal claim and deliberately seeds no jurisdiction policy.
COUNTRIES = (
    ("AD", "Andorra"),
    ("AE", "United Arab Emirates"),
    ("AF", "Afghanistan"),
    ("AG", "Antigua & Barbuda"),
    ("AI", "Anguilla"),
    ("AL", "Albania"),
    ("AM", "Armenia"),
    ("AO", "Angola"),
    ("AQ", "Antarctica"),
    ("AR", "Argentina"),
    ("AS", "Samoa (American)"),
    ("AT", "Austria"),
    ("AU", "Australia"),
    ("AW", "Aruba"),
    ("AX", "Åland Islands"),
    ("AZ", "Azerbaijan"),
    ("BA", "Bosnia & Herzegovina"),
    ("BB", "Barbados"),
    ("BD", "Bangladesh"),
    ("BE", "Belgium"),
    ("BF", "Burkina Faso"),
    ("BG", "Bulgaria"),
    ("BH", "Bahrain"),
    ("BI", "Burundi"),
    ("BJ", "Benin"),
    ("BL", "St Barthelemy"),
    ("BM", "Bermuda"),
    ("BN", "Brunei"),
    ("BO", "Bolivia"),
    ("BQ", "Caribbean NL"),
    ("BR", "Brazil"),
    ("BS", "Bahamas"),
    ("BT", "Bhutan"),
    ("BV", "Bouvet Island"),
    ("BW", "Botswana"),
    ("BY", "Belarus"),
    ("BZ", "Belize"),
    ("CA", "Canada"),
    ("CC", "Cocos (Keeling) Islands"),
    ("CD", "Congo (Dem. Rep.)"),
    ("CF", "Central African Rep."),
    ("CG", "Congo (Rep.)"),
    ("CH", "Switzerland"),
    ("CI", "Côte d'Ivoire"),
    ("CK", "Cook Islands"),
    ("CL", "Chile"),
    ("CM", "Cameroon"),
    ("CN", "China"),
    ("CO", "Colombia"),
    ("CR", "Costa Rica"),
    ("CU", "Cuba"),
    ("CV", "Cape Verde"),
    ("CW", "Curaçao"),
    ("CX", "Christmas Island"),
    ("CY", "Cyprus"),
    ("CZ", "Czech Republic"),
    ("DE", "Germany"),
    ("DJ", "Djibouti"),
    ("DK", "Denmark"),
    ("DM", "Dominica"),
    ("DO", "Dominican Republic"),
    ("DZ", "Algeria"),
    ("EC", "Ecuador"),
    ("EE", "Estonia"),
    ("EG", "Egypt"),
    ("EH", "Western Sahara"),
    ("ER", "Eritrea"),
    ("ES", "Spain"),
    ("ET", "Ethiopia"),
    ("FI", "Finland"),
    ("FJ", "Fiji"),
    ("FK", "Falkland Islands"),
    ("FM", "Micronesia"),
    ("FO", "Faroe Islands"),
    ("FR", "France"),
    ("GA", "Gabon"),
    ("GB", "Britain (UK)"),
    ("GD", "Grenada"),
    ("GE", "Georgia"),
    ("GF", "French Guiana"),
    ("GG", "Guernsey"),
    ("GH", "Ghana"),
    ("GI", "Gibraltar"),
    ("GL", "Greenland"),
    ("GM", "Gambia"),
    ("GN", "Guinea"),
    ("GP", "Guadeloupe"),
    ("GQ", "Equatorial Guinea"),
    ("GR", "Greece"),
    ("GS", "South Georgia & the South Sandwich Islands"),
    ("GT", "Guatemala"),
    ("GU", "Guam"),
    ("GW", "Guinea-Bissau"),
    ("GY", "Guyana"),
    ("HK", "Hong Kong"),
    ("HM", "Heard Island & McDonald Islands"),
    ("HN", "Honduras"),
    ("HR", "Croatia"),
    ("HT", "Haiti"),
    ("HU", "Hungary"),
    ("ID", "Indonesia"),
    ("IE", "Ireland"),
    ("IL", "Israel"),
    ("IM", "Isle of Man"),
    ("IN", "India"),
    ("IO", "British Indian Ocean Territory"),
    ("IQ", "Iraq"),
    ("IR", "Iran"),
    ("IS", "Iceland"),
    ("IT", "Italy"),
    ("JE", "Jersey"),
    ("JM", "Jamaica"),
    ("JO", "Jordan"),
    ("JP", "Japan"),
    ("KE", "Kenya"),
    ("KG", "Kyrgyzstan"),
    ("KH", "Cambodia"),
    ("KI", "Kiribati"),
    ("KM", "Comoros"),
    ("KN", "St Kitts & Nevis"),
    ("KP", "Korea (North)"),
    ("KR", "Korea (South)"),
    ("KW", "Kuwait"),
    ("KY", "Cayman Islands"),
    ("KZ", "Kazakhstan"),
    ("LA", "Laos"),
    ("LB", "Lebanon"),
    ("LC", "St Lucia"),
    ("LI", "Liechtenstein"),
    ("LK", "Sri Lanka"),
    ("LR", "Liberia"),
    ("LS", "Lesotho"),
    ("LT", "Lithuania"),
    ("LU", "Luxembourg"),
    ("LV", "Latvia"),
    ("LY", "Libya"),
    ("MA", "Morocco"),
    ("MC", "Monaco"),
    ("MD", "Moldova"),
    ("ME", "Montenegro"),
    ("MF", "St Martin (French)"),
    ("MG", "Madagascar"),
    ("MH", "Marshall Islands"),
    ("MK", "North Macedonia"),
    ("ML", "Mali"),
    ("MM", "Myanmar (Burma)"),
    ("MN", "Mongolia"),
    ("MO", "Macau"),
    ("MP", "Northern Mariana Islands"),
    ("MQ", "Martinique"),
    ("MR", "Mauritania"),
    ("MS", "Montserrat"),
    ("MT", "Malta"),
    ("MU", "Mauritius"),
    ("MV", "Maldives"),
    ("MW", "Malawi"),
    ("MX", "Mexico"),
    ("MY", "Malaysia"),
    ("MZ", "Mozambique"),
    ("NA", "Namibia"),
    ("NC", "New Caledonia"),
    ("NE", "Niger"),
    ("NF", "Norfolk Island"),
    ("NG", "Nigeria"),
    ("NI", "Nicaragua"),
    ("NL", "Netherlands"),
    ("NO", "Norway"),
    ("NP", "Nepal"),
    ("NR", "Nauru"),
    ("NU", "Niue"),
    ("NZ", "New Zealand"),
    ("OM", "Oman"),
    ("PA", "Panama"),
    ("PE", "Peru"),
    ("PF", "French Polynesia"),
    ("PG", "Papua New Guinea"),
    ("PH", "Philippines"),
    ("PK", "Pakistan"),
    ("PL", "Poland"),
    ("PM", "St Pierre & Miquelon"),
    ("PN", "Pitcairn"),
    ("PR", "Puerto Rico"),
    ("PS", "Palestine"),
    ("PT", "Portugal"),
    ("PW", "Palau"),
    ("PY", "Paraguay"),
    ("QA", "Qatar"),
    ("RE", "Réunion"),
    ("RO", "Romania"),
    ("RS", "Serbia"),
    ("RU", "Russia"),
    ("RW", "Rwanda"),
    ("SA", "Saudi Arabia"),
    ("SB", "Solomon Islands"),
    ("SC", "Seychelles"),
    ("SD", "Sudan"),
    ("SE", "Sweden"),
    ("SG", "Singapore"),
    ("SH", "St Helena"),
    ("SI", "Slovenia"),
    ("SJ", "Svalbard & Jan Mayen"),
    ("SK", "Slovakia"),
    ("SL", "Sierra Leone"),
    ("SM", "San Marino"),
    ("SN", "Senegal"),
    ("SO", "Somalia"),
    ("SR", "Suriname"),
    ("SS", "South Sudan"),
    ("ST", "Sao Tome & Principe"),
    ("SV", "El Salvador"),
    ("SX", "St Maarten (Dutch)"),
    ("SY", "Syria"),
    ("SZ", "Eswatini (Swaziland)"),
    ("TC", "Turks & Caicos Is"),
    ("TD", "Chad"),
    ("TF", "French S. Terr."),
    ("TG", "Togo"),
    ("TH", "Thailand"),
    ("TJ", "Tajikistan"),
    ("TK", "Tokelau"),
    ("TL", "East Timor"),
    ("TM", "Turkmenistan"),
    ("TN", "Tunisia"),
    ("TO", "Tonga"),
    ("TR", "Turkey"),
    ("TT", "Trinidad & Tobago"),
    ("TV", "Tuvalu"),
    ("TW", "Taiwan"),
    ("TZ", "Tanzania"),
    ("UA", "Ukraine"),
    ("UG", "Uganda"),
    ("UM", "US minor outlying islands"),
    ("US", "United States"),
    ("UY", "Uruguay"),
    ("UZ", "Uzbekistan"),
    ("VA", "Vatican City"),
    ("VC", "St Vincent"),
    ("VE", "Venezuela"),
    ("VG", "Virgin Islands (UK)"),
    ("VI", "Virgin Islands (US)"),
    ("VN", "Vietnam"),
    ("VU", "Vanuatu"),
    ("WF", "Wallis & Futuna"),
    ("WS", "Samoa (western)"),
    ("YE", "Yemen"),
    ("YT", "Mayotte"),
    ("ZA", "South Africa"),
    ("ZM", "Zambia"),
    ("ZW", "Zimbabwe"),
)


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


def upgrade() -> None:
    bind = op.get_bind()
    policy_status = postgresql.ENUM(
        "draft",
        "scheduled",
        "active",
        "retired",
        name="compliance_policy_status",
        create_type=False,
    )
    compliance_feature = postgresql.ENUM(
        "platform_access",
        "new_fan_registration",
        "creator_registration",
        "purchases",
        "subscriptions",
        "ppv",
        "live",
        "marketplace",
        "featuring",
        "marketing_email",
        "messaging",
        "adult_media",
        name="compliance_feature",
        create_type=False,
    )
    assurance = postgresql.ENUM(
        "none",
        "self_attested",
        "low",
        "medium",
        "high",
        name="age_assurance_level",
        create_type=False,
    )
    verification_status = postgresql.ENUM(
        "pending",
        "verified",
        "failed",
        "expired",
        "revoked",
        "review_required",
        name="age_verification_status",
        create_type=False,
    )
    callback_status = postgresql.ENUM(
        "received",
        "processed",
        "rejected",
        name="provider_callback_status",
        create_type=False,
    )
    probe_status = postgresql.ENUM(
        "healthy",
        "degraded",
        "unavailable",
        "misconfigured",
        name="provider_probe_status",
        create_type=False,
    )
    performer_status = postgresql.ENUM(
        "not_started",
        "pending",
        "verified",
        "failed",
        "review_required",
        "expired",
        "revoked",
        "suspended",
        name="performer_identity_status",
        create_type=False,
    )
    for enum_type in (
        policy_status,
        compliance_feature,
        assurance,
        verification_status,
        callback_status,
        probe_status,
        performer_status,
    ):
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "country_registry",
        sa.Column("code", sa.String(2), primary_key=True),
        *_timestamps(),
        sa.Column("name", sa.String(120), nullable=False),
        # The registry is a catalogue, not an assertion that FanBackstage is
        # operational in every ISO jurisdiction. Countries are activated only
        # after a reviewed effective policy exists.
        sa.Column("enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.CheckConstraint("code ~ '^[A-Z]{2}$'", name="ck_country_registry_code"),
        sa.CheckConstraint("length(btrim(name)) > 0", name="ck_country_registry_name"),
    )
    country_table = sa.table(
        "country_registry",
        sa.column("code", sa.String(2)),
        sa.column("name", sa.String(120)),
        sa.column("enabled", sa.Boolean()),
    )
    op.bulk_insert(
        country_table,
        [{"code": code, "name": name, "enabled": False} for code, name in COUNTRIES],
    )

    op.create_table(
        "compliance_policy_templates",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_timestamps(),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.String(500)),
    )
    op.create_index(
        "ix_compliance_policy_templates_key",
        "compliance_policy_templates",
        ["key"],
        unique=True,
    )
    op.create_table(
        "compliance_policy_template_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_timestamps(),
        sa.Column(
            "template_id",
            sa.Uuid(),
            sa.ForeignKey("compliance_policy_templates.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", policy_status, nullable=False),
        sa.Column("rules_json", postgresql.JSONB(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True)),
        sa.Column(
            "created_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "reviewed_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("change_reason", sa.String(500), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_compliance_template_revision_version"),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_compliance_template_revision_window",
        ),
        sa.CheckConstraint(
            "(reviewed_at IS NULL AND reviewed_by_user_id IS NULL) OR "
            "(reviewed_at IS NOT NULL AND reviewed_by_user_id IS NOT NULL)",
            name="ck_compliance_template_revision_review",
        ),
        sa.UniqueConstraint(
            "template_id", "version", name="uq_compliance_template_revision_version"
        ),
    )
    op.create_index(
        "ix_compliance_template_revision_effective",
        "compliance_policy_template_revisions",
        ["template_id", "status", "effective_from"],
    )
    op.create_index(
        "ix_compliance_policy_template_revisions_template_id",
        "compliance_policy_template_revisions",
        ["template_id"],
    )
    op.create_index(
        "ix_compliance_policy_template_revisions_status",
        "compliance_policy_template_revisions",
        ["status"],
    )
    op.create_index(
        "ix_compliance_policy_template_revisions_effective_from",
        "compliance_policy_template_revisions",
        ["effective_from"],
    )
    op.create_index(
        "ix_compliance_policy_template_revisions_is_demo",
        "compliance_policy_template_revisions",
        ["is_demo"],
    )

    op.create_table(
        "jurisdiction_policy_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_timestamps(),
        sa.Column(
            "country_code",
            sa.String(2),
            sa.ForeignKey("country_registry.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "template_revision_id",
            sa.Uuid(),
            sa.ForeignKey("compliance_policy_template_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("status", policy_status, nullable=False),
        sa.Column(
            "overrides_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("is_demo", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True)),
        sa.Column(
            "created_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column(
            "reviewed_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("change_reason", sa.String(500), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_jurisdiction_policy_revision_version"),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_jurisdiction_policy_revision_window",
        ),
        sa.CheckConstraint(
            "(reviewed_at IS NULL AND reviewed_by_user_id IS NULL) OR "
            "(reviewed_at IS NOT NULL AND reviewed_by_user_id IS NOT NULL)",
            name="ck_jurisdiction_policy_revision_review",
        ),
        sa.UniqueConstraint(
            "country_code", "version", name="uq_jurisdiction_policy_country_version"
        ),
    )
    op.create_index(
        "ix_jurisdiction_policy_effective",
        "jurisdiction_policy_revisions",
        ["country_code", "status", "effective_from"],
    )
    for column in ("country_code", "template_revision_id", "status", "effective_from", "is_demo"):
        op.create_index(
            f"ix_jurisdiction_policy_revisions_{column}",
            "jurisdiction_policy_revisions",
            [column],
        )

    op.create_table(
        "feature_flag_revisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_timestamps(),
        sa.Column("feature", compliance_feature, nullable=False),
        sa.Column("country_scope", sa.String(2), server_default="", nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True)),
        sa.Column(
            "created_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("change_reason", sa.String(500), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_feature_flag_revision_version"),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="ck_feature_flag_revision_window",
        ),
        sa.CheckConstraint(
            "country_scope = '' OR country_scope ~ '^[A-Z]{2}$'",
            name="ck_feature_flag_country_scope",
        ),
        sa.UniqueConstraint(
            "feature", "country_scope", "version", name="uq_feature_flag_scope_version"
        ),
    )
    op.create_index(
        "ix_feature_flag_effective",
        "feature_flag_revisions",
        ["feature", "country_scope", "effective_from"],
    )
    for column in ("feature", "country_scope", "effective_from", "is_demo"):
        op.create_index(f"ix_feature_flag_revisions_{column}", "feature_flag_revisions", [column])

    op.create_table(
        "anonymous_compliance_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_timestamps(),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "attached_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
        ),
        sa.Column("attached_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "(attached_user_id IS NULL AND attached_at IS NULL) OR "
            "(attached_user_id IS NOT NULL AND attached_at IS NOT NULL)",
            name="ck_anonymous_compliance_session_attachment",
        ),
    )
    op.create_index(
        "ix_anonymous_compliance_sessions_secret_hash",
        "anonymous_compliance_sessions",
        ["secret_hash"],
        unique=True,
    )
    for column in ("expires_at", "attached_user_id"):
        op.create_index(
            f"ix_anonymous_compliance_sessions_{column}",
            "anonymous_compliance_sessions",
            [column],
        )

    op.create_table(
        "age_verification_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_timestamps(),
        sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column(
            "anonymous_session_id",
            sa.Uuid(),
            sa.ForeignKey("anonymous_compliance_sessions.id", ondelete="RESTRICT"),
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_verification_id", sa.String(255)),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("state_consumed_at", sa.DateTime(timezone=True)),
        sa.Column("safe_return_path", sa.String(512), server_default="/", nullable=False),
        sa.Column(
            "country_code",
            sa.String(2),
            sa.ForeignKey("country_registry.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "applicable_policy_id",
            sa.Uuid(),
            sa.ForeignKey("jurisdiction_policy_revisions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("applicable_policy_version", sa.Integer(), nullable=False),
        sa.Column("required_minimum_age", sa.Integer(), nullable=False),
        sa.Column("achieved_minimum_age", sa.Integer()),
        sa.Column("required_assurance_level", assurance, nullable=False),
        sa.Column(
            "achieved_assurance_level",
            assurance,
            server_default=sa.text("'none'"),
            nullable=False,
        ),
        sa.Column(
            "status",
            verification_status,
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("initiated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("failed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("failure_reason_code", sa.String(96)),
        sa.Column("retryable", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "result_metadata_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "user_id IS NOT NULL OR anonymous_session_id IS NOT NULL",
            name="ck_age_verification_subject",
        ),
        sa.CheckConstraint(
            "required_minimum_age > 0 AND required_minimum_age <= 120",
            name="ck_age_verification_minimum_age",
        ),
        sa.CheckConstraint(
            "expires_at IS NULL OR verified_at IS NULL OR expires_at > verified_at",
            name="ck_age_verification_expiry",
        ),
        sa.CheckConstraint(
            "status <> 'verified' OR "
            "(verified_at IS NOT NULL AND achieved_assurance_level <> 'none' "
            "AND achieved_minimum_age IS NOT NULL AND expires_at IS NOT NULL)",
            name="ck_age_verification_verified_complete",
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR failed_at IS NOT NULL",
            name="ck_age_verification_failed_complete",
        ),
        sa.CheckConstraint(
            "status <> 'revoked' OR revoked_at IS NOT NULL",
            name="ck_age_verification_revoked_complete",
        ),
        sa.UniqueConstraint(
            "provider", "provider_verification_id", name="uq_age_verification_provider_reference"
        ),
        sa.UniqueConstraint("state_hash", name="uq_age_verification_state_hash"),
    )
    for column in (
        "user_id",
        "anonymous_session_id",
        "provider",
        "country_code",
        "applicable_policy_id",
        "status",
        "expires_at",
        "revoked_at",
    ):
        op.create_index(
            f"ix_age_verification_records_{column}", "age_verification_records", [column]
        )
    op.create_index(
        "ix_age_verification_user_status",
        "age_verification_records",
        ["user_id", "status", "created_at"],
    )
    op.create_index(
        "ix_age_verification_anonymous_status",
        "age_verification_records",
        ["anonymous_session_id", "status", "created_at"],
    )

    op.create_table(
        "age_provider_callback_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_timestamps(),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("external_event_id", sa.String(255), nullable=False),
        sa.Column(
            "verification_record_id",
            sa.Uuid(),
            sa.ForeignKey("age_verification_records.id", ondelete="RESTRICT"),
        ),
        sa.Column("status", callback_status, nullable=False),
        sa.Column("failure_reason_code", sa.String(96)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "provider", "external_event_id", name="uq_age_provider_callback_external"
        ),
    )
    for column in ("provider", "verification_record_id", "status"):
        op.create_index(
            f"ix_age_provider_callback_events_{column}",
            "age_provider_callback_events",
            [column],
        )

    op.create_table(
        "age_provider_probes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_timestamps(),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("status", probe_status, nullable=False),
        sa.Column(
            "capabilities_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("configuration_complete", sa.Boolean(), nullable=False),
        sa.Column("callback_url", sa.String(512)),
        sa.Column("error_code", sa.String(96)),
        sa.Column("probed_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ("provider", "status", "probed_at"):
        op.create_index(f"ix_age_provider_probes_{column}", "age_provider_probes", [column])

    op.create_table(
        "performer_identities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_timestamps(),
        sa.Column(
            "owner_creator_id",
            sa.Uuid(),
            sa.ForeignKey("creator_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("platform_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT")),
        sa.Column("safe_reference", sa.String(255), nullable=False),
        sa.Column(
            "country_code",
            sa.String(2),
            sa.ForeignKey("country_registry.code", ondelete="RESTRICT"),
        ),
        sa.Column(
            "created_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "owner_creator_id", "safe_reference", name="uq_performer_owner_reference"
        ),
    )
    op.create_index(
        "ix_performer_identities_owner_creator_id", "performer_identities", ["owner_creator_id"]
    )
    op.create_index(
        "ix_performer_identities_platform_user_id", "performer_identities", ["platform_user_id"]
    )

    op.create_table(
        "performer_identity_verifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_timestamps(),
        sa.Column(
            "performer_id",
            sa.Uuid(),
            sa.ForeignKey("performer_identities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_reference", sa.String(255), nullable=False),
        sa.Column("status", performer_status, nullable=False),
        sa.Column(
            "country_code",
            sa.String(2),
            sa.ForeignKey("country_registry.code", ondelete="RESTRICT"),
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("failure_reason_code", sa.String(96)),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "provider", "provider_reference", name="uq_performer_identity_provider_reference"
        ),
    )
    for column in ("performer_id", "status", "expires_at"):
        op.create_index(
            f"ix_performer_identity_verifications_{column}",
            "performer_identity_verifications",
            [column],
        )

    op.create_table(
        "performer_age_verifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_timestamps(),
        sa.Column(
            "performer_id",
            sa.Uuid(),
            sa.ForeignKey("performer_identities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_reference", sa.String(255), nullable=False),
        sa.Column("status", verification_status, nullable=False),
        sa.Column(
            "country_code",
            sa.String(2),
            sa.ForeignKey("country_registry.code", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("required_minimum_age", sa.Integer(), nullable=False),
        sa.Column("achieved_assurance_level", assurance, nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("failure_reason_code", sa.String(96)),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "required_minimum_age > 0 AND required_minimum_age <= 120",
            name="ck_performer_age_minimum_age",
        ),
        sa.UniqueConstraint(
            "provider", "provider_reference", name="uq_performer_age_provider_reference"
        ),
    )
    for column in ("performer_id", "status", "expires_at"):
        op.create_index(
            f"ix_performer_age_verifications_{column}",
            "performer_age_verifications",
            [column],
        )

    op.create_table(
        "verified_content_performers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        *_timestamps(),
        sa.Column(
            "content_id",
            sa.Uuid(),
            sa.ForeignKey("content_items.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "performer_id",
            sa.Uuid(),
            sa.ForeignKey("performer_identities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "consent_release_id",
            sa.Uuid(),
            sa.ForeignKey("consent_releases.id", ondelete="RESTRICT"),
        ),
        sa.Column(
            "identity_verification_required", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column(
            "age_verification_required", sa.Boolean(), server_default=sa.true(), nullable=False
        ),
        sa.Column("release_required", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.UniqueConstraint("content_id", "performer_id", name="uq_content_performer"),
    )
    for column in ("content_id", "performer_id", "consent_release_id"):
        op.create_index(
            f"ix_verified_content_performers_{column}",
            "verified_content_performers",
            [column],
        )

    op.add_column(
        "users",
        sa.Column(
            "country_code",
            sa.String(2),
            sa.ForeignKey("country_registry.code", ondelete="RESTRICT"),
        ),
    )
    op.create_index("ix_users_country_code", "users", ["country_code"])

    op.execute("ALTER TYPE verification_status ADD VALUE IF NOT EXISTS 'revoked'")
    op.execute("ALTER TYPE verification_status ADD VALUE IF NOT EXISTS 'suspended'")
    op.add_column(
        "creator_verifications",
        sa.Column("identity_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "creator_verifications",
        sa.Column(
            "country_code",
            sa.String(2),
            sa.ForeignKey("country_registry.code", ondelete="RESTRICT"),
        ),
    )
    op.add_column("creator_verifications", sa.Column("verified_at", sa.DateTime(timezone=True)))
    op.add_column("creator_verifications", sa.Column("expires_at", sa.DateTime(timezone=True)))
    op.add_column("creator_verifications", sa.Column("revoked_at", sa.DateTime(timezone=True)))
    op.add_column("creator_verifications", sa.Column("failure_reason_code", sa.String(96)))
    op.create_index(
        "ix_creator_verifications_country_code", "creator_verifications", ["country_code"]
    )
    op.create_index("ix_creator_verifications_expires_at", "creator_verifications", ["expires_at"])
    # The legacy row asserted adult/age verification only. It is not evidence
    # of identity/KYC, so the new identity flag deliberately retains its
    # fail-closed false default. Preserve only the factual legacy completion
    # timestamp; null expiry keeps that evidence ineligible for current access.
    op.execute(
        "UPDATE creator_verifications SET "
        "verified_at = CASE WHEN status::text = 'verified' THEN updated_at ELSE NULL END"
    )

    # Revision rows are append-only. Successor revisions may intentionally share
    # an effective window; the highest effective version is authoritative. Domain
    # services serialize version allocation with transaction advisory locks.
    op.execute(
        """
        CREATE FUNCTION reject_compliance_revision_mutation() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'compliance revision rows are immutable';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in (
        "compliance_policy_template_revisions",
        "jurisdiction_policy_revisions",
        "feature_flag_revisions",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_compliance_revision_mutation()"
        )

    op.execute(
        """
        CREATE FUNCTION prevent_compliance_subject_reassignment() RETURNS trigger AS $$
        BEGIN
          IF TG_TABLE_NAME = 'anonymous_compliance_sessions' THEN
            IF OLD.attached_user_id IS NOT NULL
               AND NEW.attached_user_id IS DISTINCT FROM OLD.attached_user_id THEN
              RAISE EXCEPTION 'anonymous compliance subject cannot be reassigned';
            END IF;
            RETURN NEW;
          END IF;

          IF NEW.anonymous_session_id IS DISTINCT FROM OLD.anonymous_session_id THEN
            RAISE EXCEPTION 'verification anonymous subject cannot be reassigned';
          END IF;
          IF NEW.user_id IS DISTINCT FROM OLD.user_id THEN
            IF OLD.user_id IS NOT NULL OR NEW.user_id IS NULL THEN
              RAISE EXCEPTION 'verification user subject cannot be reassigned';
            END IF;
            IF OLD.anonymous_session_id IS NULL OR NOT EXISTS (
              SELECT 1 FROM anonymous_compliance_sessions session
              WHERE session.id = OLD.anonymous_session_id
                AND session.attached_user_id = NEW.user_id
                AND session.revoked_at IS NULL
            ) THEN
              RAISE EXCEPTION 'verification attachment does not match anonymous session';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER trg_anonymous_compliance_subject BEFORE UPDATE ON "
        "anonymous_compliance_sessions FOR EACH ROW "
        "EXECUTE FUNCTION prevent_compliance_subject_reassignment()"
    )
    op.execute(
        "CREATE TRIGGER trg_age_verification_subject BEFORE UPDATE ON age_verification_records "
        "FOR EACH ROW EXECUTE FUNCTION prevent_compliance_subject_reassignment()"
    )


def downgrade() -> None:
    bind = op.get_bind()
    has_evidence = bind.execute(
        sa.text(
            "SELECT EXISTS ("
            "SELECT 1 FROM age_verification_records UNION ALL "
            "SELECT 1 FROM age_provider_callback_events UNION ALL "
            "SELECT 1 FROM age_provider_probes UNION ALL "
            "SELECT 1 FROM anonymous_compliance_sessions UNION ALL "
            "SELECT 1 FROM performer_identities UNION ALL "
            "SELECT 1 FROM compliance_policy_templates UNION ALL "
            "SELECT 1 FROM compliance_policy_template_revisions UNION ALL "
            "SELECT 1 FROM jurisdiction_policy_revisions UNION ALL "
            "SELECT 1 FROM feature_flag_revisions UNION ALL "
            "SELECT 1 FROM users WHERE country_code IS NOT NULL UNION ALL "
            "SELECT 1 FROM creator_verifications "
            "WHERE status::text IN ('revoked', 'suspended') "
            "OR identity_verified IS TRUE OR verified_at IS NOT NULL "
            "OR country_code IS NOT NULL OR expires_at IS NOT NULL "
            "OR revoked_at IS NOT NULL OR failure_reason_code IS NOT NULL "
            "OR identity_verified IS DISTINCT FROM adult_verified)"
        )
    ).scalar_one()
    if has_evidence:
        raise RuntimeError(
            "Cannot downgrade compliance migration with policy or verification history; "
            "use a forward corrective migration"
        )

    op.execute("DROP TRIGGER trg_age_verification_subject ON age_verification_records")
    op.execute("DROP TRIGGER trg_anonymous_compliance_subject ON anonymous_compliance_sessions")
    op.execute("DROP FUNCTION prevent_compliance_subject_reassignment()")
    for table in (
        "feature_flag_revisions",
        "jurisdiction_policy_revisions",
        "compliance_policy_template_revisions",
    ):
        op.execute(f"DROP TRIGGER trg_{table}_immutable ON {table}")
    op.execute("DROP FUNCTION reject_compliance_revision_mutation()")

    op.drop_index("ix_creator_verifications_expires_at", table_name="creator_verifications")
    op.drop_index("ix_creator_verifications_country_code", table_name="creator_verifications")
    for column in (
        "failure_reason_code",
        "revoked_at",
        "expires_at",
        "verified_at",
        "country_code",
        "identity_verified",
    ):
        op.drop_column("creator_verifications", column)
    op.execute("ALTER TYPE verification_status RENAME TO verification_status_compliance_old")
    op.execute(
        "CREATE TYPE verification_status AS ENUM "
        "('not_started', 'pending', 'verified', 'failed', 'expired', 'needs_review')"
    )
    op.execute(
        "ALTER TABLE creator_verifications ALTER COLUMN status TYPE verification_status "
        "USING status::text::verification_status"
    )
    op.execute("DROP TYPE verification_status_compliance_old")

    op.drop_index("ix_users_country_code", table_name="users")
    op.drop_column("users", "country_code")
    for table in (
        "verified_content_performers",
        "performer_age_verifications",
        "performer_identity_verifications",
        "performer_identities",
        "age_provider_callback_events",
        "age_provider_probes",
        "age_verification_records",
        "anonymous_compliance_sessions",
        "feature_flag_revisions",
        "jurisdiction_policy_revisions",
        "compliance_policy_template_revisions",
        "compliance_policy_templates",
        "country_registry",
    ):
        op.drop_table(table)

    for name in (
        "performer_identity_status",
        "provider_probe_status",
        "provider_callback_status",
        "age_verification_status",
        "age_assurance_level",
        "compliance_feature",
        "compliance_policy_status",
    ):
        postgresql.ENUM(name=name).drop(bind, checkfirst=True)
