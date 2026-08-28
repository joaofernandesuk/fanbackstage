from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.adult_access import has_current_self_attestation
from app.audit.service import record_event
from app.compliance.types import (
    ASSURANCE_STRENGTH,
    ComplianceDecision,
    JurisdictionSignals,
    PolicyOverrides,
    PolicyRules,
    normalize_country_code,
    resolve_jurisdiction_candidates,
)
from app.core.config import Settings, get_settings
from app.models.compliance import (
    AgeAssuranceLevel,
    AgeProviderProbe,
    AgeVerificationRecord,
    AgeVerificationStatus,
    AnonymousComplianceSession,
    ComplianceFeature,
    CompliancePolicyStatus,
    CompliancePolicyTemplate,
    CompliancePolicyTemplateRevision,
    CountryRegistry,
    FeatureFlagRevision,
    JurisdictionPolicyRevision,
    ProviderProbeStatus,
)
from app.models.identity import User

PUBLISHED_STATUSES = (
    CompliancePolicyStatus.scheduled,
    CompliancePolicyStatus.active,
)

FEATURE_RULE_FIELDS = {
    ComplianceFeature.platform_access: "enabled",
    ComplianceFeature.new_fan_registration: "registration_allowed",
    ComplianceFeature.creator_registration: "creator_registration_allowed",
    ComplianceFeature.purchases: "purchases_allowed",
    ComplianceFeature.subscriptions: "subscriptions_allowed",
    ComplianceFeature.ppv: "ppv_allowed",
    ComplianceFeature.live: "live_allowed",
    ComplianceFeature.marketplace: "marketplace_allowed",
    ComplianceFeature.featuring: "featuring_allowed",
    ComplianceFeature.marketing_email: "marketing_email_allowed",
    ComplianceFeature.messaging: "messaging_allowed",
    ComplianceFeature.adult_media: "enabled",
}


class CompliancePolicyError(ValueError):
    pass


@dataclass(frozen=True)
class EffectivePolicy:
    jurisdiction_revision: JurisdictionPolicyRevision
    template_revision: CompliancePolicyTemplateRevision
    rules: PolicyRules


@dataclass(frozen=True)
class VerificationState:
    status: AgeVerificationStatus | None
    achieved_assurance_level: AgeAssuranceLevel
    achieved_minimum_age: int | None
    country_code: str | None
    expires_at: datetime | None
    grace_active: bool = False


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current if current.tzinfo else current.replace(tzinfo=UTC)


def _provider_expiry_bound(record: AgeVerificationRecord) -> datetime | None:
    """Return the normalized upstream hard bound, preserving legacy fail-closed rows."""

    metadata = record.result_metadata_json or {}
    if "provider_expires_at" not in metadata:
        # Older rows stored only the already-composed effective expiry. Treat it
        # as a hard bound rather than accidentally extending historical evidence.
        return record.expires_at
    value = metadata.get("provider_expires_at")
    if value == "not_provided":
        return None
    if not isinstance(value, str):
        return record.expires_at
    try:
        return _now(datetime.fromisoformat(value))
    except ValueError:
        return record.expires_at


def _latest_revocation_tombstone(
    records: list[AgeVerificationRecord],
) -> tuple[datetime, AgeVerificationRecord] | None:
    """Return the chronologically latest revocation without trusting row creation order."""

    tombstones: list[tuple[datetime, str, AgeVerificationRecord]] = []
    for record in records:
        if record.revoked_at is not None:
            revoked_at = _now(record.revoked_at)
        elif record.status is AgeVerificationStatus.revoked:
            # A legacy/inconsistent revoked row without its lifecycle timestamp
            # remains fail-closed. Updated-at is the closest durable authority
            # boundary; creation time is the final deterministic fallback.
            revoked_at = _now(record.updated_at or record.created_at)
        else:
            continue
        tombstones.append((revoked_at, str(record.id), record))
    if not tombstones:
        return None
    revoked_at, _, record = max(tombstones, key=lambda item: (item[0], item[1]))
    return revoked_at, record


def _verified_strictly_after(
    record: AgeVerificationRecord,
    cutoff: datetime,
) -> bool:
    """Only a successful, non-revoked result after the tombstone restores authority."""

    return bool(
        record.status is AgeVerificationStatus.verified
        and record.revoked_at is None
        and record.verified_at is not None
        and _now(record.verified_at) > cutoff
    )


def _validate_publish_fields(
    status: CompliancePolicyStatus,
    reviewed_at: datetime | None,
    reviewed_by_user_id: UUID | None,
) -> None:
    if status in PUBLISHED_STATUSES and (reviewed_at is None or reviewed_by_user_id is None):
        raise CompliancePolicyError("Published policy revisions require an explicit review")


def _resolved_policy_rules(
    template: CompliancePolicyTemplateRevision,
    jurisdiction: JurisdictionPolicyRevision,
) -> PolicyRules | None:
    try:
        base = PolicyRules.model_validate(template.rules_json).model_dump()
        overrides = PolicyOverrides.model_validate(jurisdiction.overrides_json)
        for field_name in overrides.model_fields_set:
            base[field_name] = getattr(overrides, field_name)
        return PolicyRules.model_validate(base)
    except ValueError:
        return None


async def create_policy_template(
    db: AsyncSession,
    *,
    key: str,
    name: str,
    description: str | None,
    actor_user_id: UUID,
    change_reason: str,
) -> CompliancePolicyTemplate:
    normalized_key = key.strip().lower()
    if not normalized_key or len(normalized_key) > 64:
        raise CompliancePolicyError("Policy template key is invalid")
    if not name.strip() or not change_reason.strip():
        raise CompliancePolicyError("Name and change reason are required")
    if await db.scalar(
        select(CompliancePolicyTemplate.id).where(CompliancePolicyTemplate.key == normalized_key)
    ):
        raise CompliancePolicyError("Policy template key already exists")
    template = CompliancePolicyTemplate(
        key=normalized_key,
        name=name.strip(),
        description=description.strip() if description else None,
    )
    db.add(template)
    await db.flush()
    await record_event(
        db,
        "compliance.policy_template_created",
        actor_user_id=actor_user_id,
        target_type="compliance_policy_template",
        target_id=str(template.id),
        metadata={"change_reason": change_reason.strip(), "template_key": normalized_key},
    )
    return template


async def register_country(
    db: AsyncSession,
    *,
    code: str,
    name: str,
    actor_user_id: UUID,
    change_reason: str,
) -> CountryRegistry:
    country = normalize_country_code(code)
    assert country is not None
    normalized_name = name.strip()
    if not normalized_name or len(normalized_name) > 120 or not change_reason.strip():
        raise CompliancePolicyError("Country name and change reason are required")
    existing = await db.get(CountryRegistry, country)
    if existing is not None:
        raise CompliancePolicyError("Country already exists in the registry")
    # Registration makes the jurisdiction available for policy authoring. It
    # does not advertise or serve it until a reviewed policy is effective.
    row = CountryRegistry(code=country, name=normalized_name, enabled=False)
    db.add(row)
    await record_event(
        db,
        "compliance.country_registered",
        actor_user_id=actor_user_id,
        target_type="country_registry",
        target_id=country,
        metadata={"country_code": country, "change_reason": change_reason.strip()},
    )
    return row


async def set_country_enabled(
    db: AsyncSession,
    *,
    code: str,
    enabled: bool,
    actor_user_id: UUID,
    change_reason: str,
) -> CountryRegistry:
    country = normalize_country_code(code)
    assert country is not None
    if not change_reason.strip():
        raise CompliancePolicyError("Change reason is required")
    row = await db.scalar(
        select(CountryRegistry).where(CountryRegistry.code == country).with_for_update()
    )
    if row is None:
        raise CompliancePolicyError("Country was not found in the registry")
    if enabled and await effective_policy_for_country(db, country) is None:
        raise CompliancePolicyError("Country cannot be enabled without a reviewed effective policy")
    before = row.enabled
    row.enabled = enabled
    await record_event(
        db,
        "compliance.country_availability_changed",
        actor_user_id=actor_user_id,
        target_type="country_registry",
        target_id=country,
        metadata={
            "country_code": country,
            "before": before,
            "after": enabled,
            "change_reason": change_reason.strip(),
        },
    )
    return row


async def set_account_country(
    db: AsyncSession,
    *,
    user_id: UUID,
    country_code: str,
    actor_user_id: UUID,
    change_reason: str,
    source: str,
) -> User:
    """Establish or change account jurisdiction through an audited trusted command."""

    country = normalize_country_code(country_code)
    assert country is not None
    if not change_reason.strip() or source not in {"trusted_request", "operator_review"}:
        raise CompliancePolicyError("A valid country-change reason and source are required")
    user = await db.scalar(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None:
        raise CompliancePolicyError("Account was not found")
    registry = await db.get(CountryRegistry, country)
    if (
        registry is None
        or not registry.enabled
        or await effective_policy_for_country(db, country) is None
    ):
        raise CompliancePolicyError(
            "Account country requires an enabled reviewed effective jurisdiction policy"
        )
    before = user.country_code
    if before == country:
        return user
    user.country_code = country
    await record_event(
        db,
        "compliance.account_country_changed",
        actor_user_id=actor_user_id,
        target_type="user",
        target_id=str(user.id),
        metadata={
            # Audit metadata deliberately omits null values, so use an
            # explicit sentinel to retain the legacy-null transition fact.
            "before_country": before or "UNSET",
            "after_country": country,
            "source": source,
            "change_reason": change_reason.strip(),
        },
    )
    return user


async def create_template_revision(
    db: AsyncSession,
    *,
    template_id: UUID,
    rules: PolicyRules,
    status: CompliancePolicyStatus,
    effective_from: datetime,
    effective_until: datetime | None,
    actor_user_id: UUID,
    reviewed_at: datetime | None,
    reviewed_by_user_id: UUID | None,
    change_reason: str,
    is_demo: bool = False,
) -> CompliancePolicyTemplateRevision:
    if not change_reason.strip():
        raise CompliancePolicyError("Change reason is required")
    _validate_publish_fields(status, reviewed_at, reviewed_by_user_id)
    template = await db.scalar(
        select(CompliancePolicyTemplate)
        .where(CompliancePolicyTemplate.id == template_id)
        .with_for_update()
    )
    if template is None:
        raise CompliancePolicyError("Policy template was not found")
    await db.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(f"compliance-template:{template_id}", 0)
            )
        )
    )
    start, end = _now(effective_from), _now(effective_until) if effective_until else None
    if end is not None and end <= start:
        raise CompliancePolicyError("Effective policy window is invalid")
    previous = await db.scalar(
        select(CompliancePolicyTemplateRevision)
        .where(CompliancePolicyTemplateRevision.template_id == template_id)
        .order_by(CompliancePolicyTemplateRevision.version.desc())
        .limit(1)
    )
    version = (
        await db.scalar(
            select(func.coalesce(func.max(CompliancePolicyTemplateRevision.version), 0)).where(
                CompliancePolicyTemplateRevision.template_id == template_id
            )
        )
    ) + 1
    revision = CompliancePolicyTemplateRevision(
        template_id=template_id,
        version=version,
        status=status,
        rules_json=rules.model_dump(mode="json"),
        is_demo=is_demo,
        effective_from=start,
        effective_until=end,
        created_by_user_id=actor_user_id,
        reviewed_at=_now(reviewed_at) if reviewed_at else None,
        reviewed_by_user_id=reviewed_by_user_id,
        change_reason=change_reason.strip(),
    )
    db.add(revision)
    await db.flush()
    await _audit_revision_created(
        db,
        event_type="compliance.template_revision_created",
        target_type="compliance_policy_template_revision",
        target_id=revision.id,
        actor_user_id=actor_user_id,
        version=version,
        status=status,
        is_demo=is_demo,
        change_reason=change_reason,
        rules=rules.model_dump(mode="json"),
        previous_rules=previous.rules_json if previous else None,
        effective_from=start,
        effective_until=end,
        previous_effective_from=previous.effective_from if previous else None,
        previous_effective_until=previous.effective_until if previous else None,
    )
    return revision


async def create_jurisdiction_revision(
    db: AsyncSession,
    *,
    country_code: str,
    template_revision_id: UUID,
    overrides: PolicyOverrides,
    status: CompliancePolicyStatus,
    effective_from: datetime,
    effective_until: datetime | None,
    actor_user_id: UUID,
    reviewed_at: datetime | None,
    reviewed_by_user_id: UUID | None,
    change_reason: str,
    is_demo: bool = False,
) -> JurisdictionPolicyRevision:
    country = normalize_country_code(country_code)
    assert country is not None
    if not change_reason.strip():
        raise CompliancePolicyError("Change reason is required")
    _validate_publish_fields(status, reviewed_at, reviewed_by_user_id)
    registry = await db.scalar(
        select(CountryRegistry).where(CountryRegistry.code == country).with_for_update()
    )
    # Policy authoring intentionally precedes operational activation. Runtime
    # continues to require ``enabled``; this allows a country to remain
    # fail-closed until its reviewed effective revision is ready.
    if registry is None:
        raise CompliancePolicyError("Country was not found in the ISO registry")
    template_revision = await db.get(CompliancePolicyTemplateRevision, template_revision_id)
    if template_revision is None:
        raise CompliancePolicyError("Template revision was not found")
    await db.execute(
        select(
            func.pg_advisory_xact_lock(func.hashtextextended(f"compliance-country:{country}", 0))
        )
    )
    start, end = _now(effective_from), _now(effective_until) if effective_until else None
    if end is not None and end <= start:
        raise CompliancePolicyError("Effective policy window is invalid")
    previous = await db.scalar(
        select(JurisdictionPolicyRevision)
        .where(JurisdictionPolicyRevision.country_code == country)
        .order_by(JurisdictionPolicyRevision.version.desc())
        .limit(1)
    )
    version = (
        await db.scalar(
            select(func.coalesce(func.max(JurisdictionPolicyRevision.version), 0)).where(
                JurisdictionPolicyRevision.country_code == country
            )
        )
    ) + 1
    # exclude_unset preserves an explicitly supplied null re-verification interval.
    overrides_json = overrides.model_dump(mode="json", exclude_unset=True)
    resolved_rules = PolicyRules.model_validate(
        {
            **PolicyRules.model_validate(template_revision.rules_json).model_dump(),
            **{field: getattr(overrides, field) for field in overrides.model_fields_set},
        }
    ).model_dump(mode="json")
    previous_rules = None
    if previous is not None:
        previous_template = await db.get(
            CompliancePolicyTemplateRevision, previous.template_revision_id
        )
        if previous_template is not None:
            previous_overrides = PolicyOverrides.model_validate(previous.overrides_json)
            previous_rules = PolicyRules.model_validate(
                {
                    **PolicyRules.model_validate(previous_template.rules_json).model_dump(),
                    **{
                        field: getattr(previous_overrides, field)
                        for field in previous_overrides.model_fields_set
                    },
                }
            ).model_dump(mode="json")
    revision = JurisdictionPolicyRevision(
        country_code=country,
        version=version,
        template_revision_id=template_revision_id,
        status=status,
        overrides_json=overrides_json,
        is_demo=is_demo,
        effective_from=start,
        effective_until=end,
        created_by_user_id=actor_user_id,
        reviewed_at=_now(reviewed_at) if reviewed_at else None,
        reviewed_by_user_id=reviewed_by_user_id,
        change_reason=change_reason.strip(),
    )
    db.add(revision)
    await db.flush()
    await _audit_revision_created(
        db,
        event_type="compliance.jurisdiction_revision_created",
        target_type="jurisdiction_policy_revision",
        target_id=revision.id,
        actor_user_id=actor_user_id,
        version=version,
        status=status,
        is_demo=is_demo,
        change_reason=change_reason,
        rules=resolved_rules,
        previous_rules=previous_rules,
        effective_from=start,
        effective_until=end,
        previous_effective_from=previous.effective_from if previous else None,
        previous_effective_until=previous.effective_until if previous else None,
    )
    return revision


async def create_feature_flag_revision(
    db: AsyncSession,
    *,
    feature: ComplianceFeature,
    country_scope: str | None,
    enabled: bool,
    effective_from: datetime,
    effective_until: datetime | None,
    actor_user_id: UUID,
    change_reason: str,
    is_demo: bool = False,
) -> FeatureFlagRevision:
    scope = normalize_country_code(country_scope) if country_scope else ""
    if not change_reason.strip():
        raise CompliancePolicyError("Change reason is required")
    if scope:
        registry = await db.scalar(
            select(CountryRegistry).where(CountryRegistry.code == scope).with_for_update()
        )
        if registry is None or not registry.enabled:
            raise CompliancePolicyError("Country is not enabled in the ISO registry")
    await db.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(f"compliance-feature:{feature.value}:{scope}", 0)
            )
        )
    )
    start, end = _now(effective_from), _now(effective_until) if effective_until else None
    if end is not None and end <= start:
        raise CompliancePolicyError("Effective feature window is invalid")
    previous = await db.scalar(
        select(FeatureFlagRevision)
        .where(
            FeatureFlagRevision.feature == feature,
            FeatureFlagRevision.country_scope == scope,
        )
        .order_by(FeatureFlagRevision.version.desc())
        .limit(1)
    )
    version = await db.scalar(
        select(func.coalesce(func.max(FeatureFlagRevision.version), 0)).where(
            FeatureFlagRevision.feature == feature,
            FeatureFlagRevision.country_scope == scope,
        )
    )
    revision = FeatureFlagRevision(
        feature=feature,
        country_scope=scope,
        version=version + 1,
        enabled=enabled,
        is_demo=is_demo,
        effective_from=start,
        effective_until=end,
        created_by_user_id=actor_user_id,
        change_reason=change_reason.strip(),
    )
    db.add(revision)
    await db.flush()
    await record_event(
        db,
        "compliance.feature_flag_revision_created",
        actor_user_id=actor_user_id,
        target_type="feature_flag_revision",
        target_id=str(revision.id),
        metadata={
            "feature": feature.value,
            "country_scope": scope or "global",
            "enabled": enabled,
            "version": revision.version,
            "is_demo": is_demo,
            "change_reason": change_reason.strip(),
            "changes": [
                {
                    "field": "enabled",
                    "before": previous.enabled if previous else None,
                    "after": enabled,
                }
            ],
            "effective_from": start.isoformat(),
            "effective_until": end.isoformat() if end else "unbounded",
            "previous_effective_from": (
                previous.effective_from.isoformat() if previous else "none"
            ),
            "previous_effective_until": (
                previous.effective_until.isoformat()
                if previous and previous.effective_until
                else "unbounded"
                if previous
                else "none"
            ),
        },
    )
    return revision


async def _audit_revision_created(
    db: AsyncSession,
    *,
    event_type: str,
    target_type: str,
    target_id: UUID,
    actor_user_id: UUID,
    version: int,
    status: CompliancePolicyStatus,
    is_demo: bool,
    change_reason: str,
    rules: dict,
    previous_rules: dict | None,
    effective_from: datetime,
    effective_until: datetime | None,
    previous_effective_from: datetime | None,
    previous_effective_until: datetime | None,
) -> None:
    # Field names are values, rather than metadata keys, so the central scrubber
    # can retain a useful before/after audit without storing provider evidence.
    before = previous_rules or {}
    changes = [
        {"field": key, "before": before.get(key), "after": rules.get(key)}
        for key in sorted(set(before) | set(rules))
        if before.get(key) != rules.get(key)
    ]
    await record_event(
        db,
        event_type,
        actor_user_id=actor_user_id,
        target_type=target_type,
        target_id=str(target_id),
        metadata={
            "version": version,
            "status": status.value,
            "is_demo": is_demo,
            "change_reason": change_reason.strip(),
            "changes": changes,
            "effective_from": effective_from.isoformat(),
            "effective_until": effective_until.isoformat() if effective_until else "unbounded",
            "previous_effective_from": (
                previous_effective_from.isoformat() if previous_effective_from else "none"
            ),
            "previous_effective_until": (
                previous_effective_until.isoformat()
                if previous_effective_until
                else "unbounded"
                if previous_effective_from
                else "none"
            ),
        },
    )


async def effective_policy_for_country(
    db: AsyncSession, country_code: str, *, now: datetime | None = None
) -> EffectivePolicy | None:
    country = normalize_country_code(country_code)
    assert country is not None
    current = _now(now)
    # Published revisions are append-only. A later effective version is an
    # explicit replacement and wins deterministically without mutating history.
    jurisdiction = await db.scalar(
        select(JurisdictionPolicyRevision)
        .where(
            JurisdictionPolicyRevision.country_code == country,
            JurisdictionPolicyRevision.status.in_(PUBLISHED_STATUSES),
            JurisdictionPolicyRevision.reviewed_at.is_not(None),
            JurisdictionPolicyRevision.reviewed_by_user_id.is_not(None),
            JurisdictionPolicyRevision.effective_from <= current,
            or_(
                JurisdictionPolicyRevision.effective_until.is_(None),
                JurisdictionPolicyRevision.effective_until > current,
            ),
        )
        .order_by(JurisdictionPolicyRevision.version.desc())
        .limit(1)
    )
    if jurisdiction is None:
        return None
    template = await db.get(CompliancePolicyTemplateRevision, jurisdiction.template_revision_id)
    if (
        template is None
        or template.status not in PUBLISHED_STATUSES
        or template.reviewed_at is None
        or template.reviewed_by_user_id is None
        or template.effective_from > current
        or (template.effective_until is not None and template.effective_until <= current)
    ):
        return None
    rules = _resolved_policy_rules(template, jurisdiction)
    if rules is None:
        return None
    return EffectivePolicy(jurisdiction, template, rules)


async def policy_for_revision(db: AsyncSession, revision_id: UUID) -> EffectivePolicy | None:
    """Load the immutable reviewed policy snapshot attached to evidence.

    Effective windows are intentionally not re-evaluated here: a verification
    record binds the exact revision that was effective when its provider flow
    started. The revision remains append-only even after a successor publishes.
    """

    jurisdiction = await db.get(JurisdictionPolicyRevision, revision_id)
    if (
        jurisdiction is None
        or jurisdiction.status not in PUBLISHED_STATUSES
        or jurisdiction.reviewed_at is None
        or jurisdiction.reviewed_by_user_id is None
    ):
        return None
    template = await db.get(CompliancePolicyTemplateRevision, jurisdiction.template_revision_id)
    if (
        template is None
        or template.status not in PUBLISHED_STATUSES
        or template.reviewed_at is None
        or template.reviewed_by_user_id is None
    ):
        return None
    rules = _resolved_policy_rules(template, jurisdiction)
    return EffectivePolicy(jurisdiction, template, rules) if rules is not None else None


async def _effective_feature_override(
    db: AsyncSession,
    feature: ComplianceFeature,
    country_code: str,
    *,
    now: datetime,
) -> tuple[bool | None, bool]:
    async def scope_value(scope: str) -> tuple[bool | None, bool]:
        row = await db.scalar(
            select(FeatureFlagRevision)
            .where(
                FeatureFlagRevision.feature == feature,
                FeatureFlagRevision.country_scope == scope,
                FeatureFlagRevision.effective_from <= now,
                or_(
                    FeatureFlagRevision.effective_until.is_(None),
                    FeatureFlagRevision.effective_until > now,
                ),
            )
            .order_by(FeatureFlagRevision.version.desc())
            .limit(1)
        )
        return (row.enabled if row else None), False

    country_value, conflict = await scope_value(country_code)
    if conflict or country_value is not None:
        return country_value, conflict
    return await scope_value("")


async def _verification_state(
    db: AsyncSession,
    *,
    user: User | None,
    anonymous_session_secret: str | None,
    country_code: str,
    now: datetime,
    required_minimum_age: int,
    required_assurance_level: AgeAssuranceLevel,
    reverify_after_days: int | None,
    grace_period_days: int,
) -> VerificationState:
    subject_filters = []
    anonymous_expiry: datetime | None = None
    if user is not None:
        subject_filters.append(AgeVerificationRecord.user_id == user.id)
    if anonymous_session_secret:
        secret_hash = sha256(anonymous_session_secret.encode()).hexdigest()
        session = await db.scalar(
            select(AnonymousComplianceSession).where(
                AnonymousComplianceSession.secret_hash == secret_hash,
                AnonymousComplianceSession.revoked_at.is_(None),
                AnonymousComplianceSession.expires_at > now,
            )
        )
        if session is not None and (
            session.attached_user_id is None
            or (user is not None and session.attached_user_id == user.id)
        ):
            subject_filters.append(AgeVerificationRecord.anonymous_session_id == session.id)
            anonymous_expiry = session.expires_at
    if not subject_filters:
        return VerificationState(None, AgeAssuranceLevel.none, None, None, None)
    records = (
        await db.scalars(
            select(AgeVerificationRecord)
            .where(or_(*subject_filters))
            .order_by(AgeVerificationRecord.created_at.desc(), AgeVerificationRecord.id.desc())
        )
    ).all()
    if not records:
        return VerificationState(None, AgeAssuranceLevel.none, None, None, None)
    tombstone = _latest_revocation_tombstone(records)
    if tombstone is not None:
        revocation_cutoff, revocation = tombstone
        candidate_records = [
            record for record in records if _verified_strictly_after(record, revocation_cutoff)
        ]
    else:
        revocation = None
        candidate_records = records
    if revocation is not None and not candidate_records:
        return VerificationState(
            AgeVerificationStatus.revoked,
            revocation.achieved_assurance_level,
            revocation.achieved_minimum_age,
            revocation.country_code,
            revocation.expires_at,
        )
    # A revocation is a tombstone: evidence verified at or before its actual
    # revocation time can never become current again. Pending/failed attempts do
    # not clear it, irrespective of row creation order or stale verified fields.
    latest = candidate_records[0]
    latest_effective_expiry = latest.expires_at
    valid_candidates: list[
        tuple[tuple[bool, int, int, datetime, datetime, str], VerificationState]
    ] = []
    for record in candidate_records:
        provider_expiry = _provider_expiry_bound(record)
        expires_at = provider_expiry
        grace_active = False
        if record.verified_at and reverify_after_days is not None:
            policy_expiry = record.verified_at + timedelta(days=reverify_after_days)
            # Grace only extends the platform's re-verification interval. It
            # cannot extend an upstream expiry or revive failed/revoked state.
            policy_terminal_expiry = policy_expiry + timedelta(days=grace_period_days)
            grace_active = bool(
                grace_period_days
                and policy_expiry <= now < policy_terminal_expiry
                and (provider_expiry is None or provider_expiry > now)
            )
            expires_at = (
                min(expires_at, policy_terminal_expiry) if expires_at else policy_terminal_expiry
            )
        if anonymous_expiry and record.anonymous_session_id:
            expires_at = min(expires_at, anonymous_expiry) if expires_at else anonymous_expiry
        if (
            record.status is AgeVerificationStatus.verified
            and record.revoked_at is None
            and expires_at is not None
            and expires_at > now
        ):
            state = VerificationState(
                record.status,
                record.achieved_assurance_level,
                record.achieved_minimum_age,
                record.country_code,
                expires_at,
                grace_active,
            )
            strength = ASSURANCE_STRENGTH[record.achieved_assurance_level]
            threshold = record.achieved_minimum_age or 0
            satisfies = (
                strength >= ASSURANCE_STRENGTH[required_assurance_level]
                and threshold >= required_minimum_age
            )
            valid_candidates.append(
                (
                    (
                        satisfies,
                        strength,
                        threshold,
                        expires_at,
                        _now(record.created_at),
                        str(record.id),
                    ),
                    state,
                )
            )
        if record.id == latest.id:
            latest_effective_expiry = expires_at
    if valid_candidates:
        return max(valid_candidates, key=lambda candidate: candidate[0])[1]
    status = latest.status
    if (
        status is AgeVerificationStatus.verified
        and latest_effective_expiry
        and latest_effective_expiry <= now
    ):
        status = AgeVerificationStatus.expired
    return VerificationState(
        status,
        latest.achieved_assurance_level,
        latest.achieved_minimum_age,
        latest.country_code,
        latest_effective_expiry,
    )


async def current_verification_country(
    db: AsyncSession,
    *,
    user: User | None,
    anonymous_session_secret: str | None,
    now: datetime,
) -> str | None:
    """Return the newest valid evidence country used by assurance resolution."""

    filters = []
    if user is not None:
        filters.append(AgeVerificationRecord.user_id == user.id)
    if anonymous_session_secret:
        session = await db.scalar(
            select(AnonymousComplianceSession).where(
                AnonymousComplianceSession.secret_hash
                == sha256(anonymous_session_secret.encode()).hexdigest(),
                AnonymousComplianceSession.revoked_at.is_(None),
                AnonymousComplianceSession.expires_at > now,
            )
        )
        if session is not None and (
            session.attached_user_id is None
            or (user is not None and session.attached_user_id == user.id)
        ):
            filters.append(AgeVerificationRecord.anonymous_session_id == session.id)
    if not filters:
        return None
    records = (
        await db.scalars(
            select(AgeVerificationRecord)
            .where(or_(*filters))
            .order_by(AgeVerificationRecord.created_at.desc(), AgeVerificationRecord.id.desc())
        )
    ).all()
    if not records:
        return None
    tombstone = _latest_revocation_tombstone(records)
    if tombstone is not None:
        revocation_cutoff, _ = tombstone
        candidates = [
            record for record in records if _verified_strictly_after(record, revocation_cutoff)
        ]
        if not candidates:
            return None
    else:
        candidates = records
    return next(
        (
            record.country_code
            for record in candidates
            if record.status is AgeVerificationStatus.verified
            and record.revoked_at is None
            and record.expires_at is not None
            and record.expires_at > now
        ),
        None,
    )


def _decision(
    *,
    allowed: bool,
    code: str,
    action: str | None,
    reason: str,
    feature: ComplianceFeature,
    jurisdiction: str | None,
    policy: EffectivePolicy | None,
    required_minimum_age: int | None = None,
    required_assurance: AgeAssuranceLevel = AgeAssuranceLevel.none,
    verification: VerificationState | None = None,
    age_access_allowed: bool = False,
    feature_allowed: bool = False,
    country_conflict: bool = False,
) -> ComplianceDecision:
    verification = verification or VerificationState(None, AgeAssuranceLevel.none, None, None, None)
    return ComplianceDecision(
        allowed=allowed,
        code=code,
        action=action,
        reason=reason,
        feature=feature,
        jurisdiction=jurisdiction,
        policy_id=policy.jurisdiction_revision.id if policy else None,
        policy_version=policy.jurisdiction_revision.version if policy else None,
        required_minimum_age=required_minimum_age,
        required_assurance_level=required_assurance,
        achieved_assurance_level=verification.achieved_assurance_level,
        age_access_allowed=age_access_allowed,
        feature_allowed=feature_allowed,
        country_conflict=country_conflict,
        verification_expires_at=verification.expires_at,
    )


async def resolve_compliance_decision(
    db: AsyncSession,
    *,
    user: User | None,
    feature: ComplianceFeature,
    signals: JurisdictionSignals | None = None,
    adult_restricted: bool = False,
    anonymous_session_secret: str | None = None,
    legacy_self_attested: bool | None = None,
    legacy_self_attested_expires_at: datetime | None = None,
    now: datetime | None = None,
) -> ComplianceDecision:
    """Resolve one fail-closed jurisdiction, feature, and age-assurance decision.

    `platform_access=false` is the canonical global/jurisdiction maintenance
    control; there is intentionally no competing maintenance-mode authority.
    """

    current = _now(now)
    settings = get_settings()
    supplied = signals or JurisdictionSignals()
    persisted_account_country = getattr(user, "country_code", None) if user else None
    try:
        normalized_persisted_account = normalize_country_code(persisted_account_country)
        normalized_supplied_account = normalize_country_code(supplied.account_country)
    except ValueError:
        return _decision(
            allowed=False,
            code="JURISDICTION_UNRESOLVED",
            action="CONTACT_SUPPORT",
            reason="Jurisdiction signals are invalid",
            feature=feature,
            jurisdiction=None,
            policy=None,
        )
    if (
        normalized_persisted_account
        and normalized_supplied_account
        and normalized_persisted_account != normalized_supplied_account
    ):
        return _decision(
            allowed=False,
            code="COUNTRY_SIGNAL_CONFLICT",
            action="CONTACT_SUPPORT",
            reason="Jurisdiction signals conflict",
            feature=feature,
            jurisdiction=normalized_persisted_account,
            policy=None,
            country_conflict=True,
        )
    supplied = JurisdictionSignals(
        verification_country=supplied.verification_country,
        kyc_country=supplied.kyc_country,
        billing_country=supplied.billing_country,
        trusted_proxy_country=supplied.trusted_proxy_country,
        request_country=supplied.request_country,
        account_country=normalized_supplied_account or normalized_persisted_account,
        selected_country=supplied.selected_country,
    )
    durable_verification_country = await current_verification_country(
        db,
        user=user,
        anonymous_session_secret=anonymous_session_secret,
        now=current,
    )
    try:
        supplied_verification_country = normalize_country_code(supplied.verification_country)
        current_signals = JurisdictionSignals(
            verification_country=supplied_verification_country,
            kyc_country=supplied.kyc_country,
            billing_country=supplied.billing_country,
            trusted_proxy_country=supplied.trusted_proxy_country,
            request_country=supplied.request_country,
            account_country=supplied.account_country,
            selected_country=supplied.selected_country,
        )
        # A durable result records where evidence was collected; it is not an
        # eternal veto when current account/GeoIP/KYC/billing authorities agree
        # on a new jurisdiction. Resolve those current authorities first, then
        # use historical provider provenance only as a fallback. The existing
        # age/assurance result is evaluated against the resulting current policy.
        countries = resolve_jurisdiction_candidates(
            current_signals,
            fallback_country=None,
            allow_untrusted_selection=settings.environment in {"development", "test"},
        )
        if not countries:
            fallback_signals = JurisdictionSignals(
                verification_country=durable_verification_country,
                selected_country=supplied.selected_country,
            )
            countries = resolve_jurisdiction_candidates(
                fallback_signals,
                # Fallback is an operational authority for anonymous and
                # server-to-server requests. It must never silently assert a
                # migrated authenticated account's missing country as fact.
                fallback_country=(
                    settings.effective_compliance_fallback_country()
                    if user is None or durable_verification_country is not None
                    else None
                ),
                allow_untrusted_selection=settings.environment in {"development", "test"},
            )
    except ValueError:
        return _decision(
            allowed=False,
            code="JURISDICTION_UNRESOLVED",
            action="CONTACT_SUPPORT",
            reason="Jurisdiction signals are invalid",
            feature=feature,
            jurisdiction=None,
            policy=None,
        )
    if not countries:
        return _decision(
            allowed=False,
            code="JURISDICTION_UNRESOLVED",
            action="CONTACT_SUPPORT",
            reason="Jurisdiction could not be resolved",
            feature=feature,
            jurisdiction=None,
            policy=None,
        )
    if len(countries) > 1:
        return _decision(
            allowed=False,
            code="COUNTRY_SIGNAL_CONFLICT",
            action="CONTACT_SUPPORT",
            reason="Jurisdiction signals conflict",
            feature=feature,
            jurisdiction=countries[0],
            policy=None,
            country_conflict=True,
        )
    country = countries[0]
    registry = await db.get(CountryRegistry, country)
    if registry is None or not registry.enabled:
        return _decision(
            allowed=False,
            code="JURISDICTION_BLOCKED",
            action="CONTACT_SUPPORT",
            reason="Platform access is unavailable in this jurisdiction",
            feature=feature,
            jurisdiction=country,
            policy=None,
        )
    policy = await effective_policy_for_country(db, country, now=current)
    if policy is None:
        return _decision(
            allowed=False,
            code="POLICY_UNAVAILABLE",
            action="RETRY_LATER",
            reason="No reviewed effective policy is available",
            feature=feature,
            jurisdiction=country,
            policy=None,
        )
    rules = policy.rules
    if not rules.enabled:
        return _decision(
            allowed=False,
            code="JURISDICTION_BLOCKED",
            action="CONTACT_SUPPORT",
            reason="Platform access is unavailable in this jurisdiction",
            feature=feature,
            jurisdiction=country,
            policy=policy,
            required_minimum_age=rules.minimum_age,
            required_assurance=rules.required_assurance_level,
        )
    platform_override, platform_override_conflict = await _effective_feature_override(
        db,
        ComplianceFeature.platform_access,
        country,
        now=current,
    )
    if platform_override_conflict:
        return _decision(
            allowed=False,
            code="POLICY_UNAVAILABLE",
            action="RETRY_LATER",
            reason="Platform access configuration conflicts",
            feature=feature,
            jurisdiction=country,
            policy=policy,
            required_minimum_age=rules.minimum_age,
            required_assurance=rules.required_assurance_level,
        )
    if platform_override is False:
        return _decision(
            allowed=False,
            code="FEATURE_UNAVAILABLE",
            action="RETRY_LATER",
            reason="Platform access is temporarily unavailable",
            feature=feature,
            jurisdiction=country,
            policy=policy,
            required_minimum_age=rules.minimum_age,
            required_assurance=rules.required_assurance_level,
            feature_allowed=False,
        )
    feature_allowed = bool(getattr(rules, FEATURE_RULE_FIELDS[feature]))
    override, override_conflict = await _effective_feature_override(
        db, feature, country, now=current
    )
    if override_conflict:
        return _decision(
            allowed=False,
            code="POLICY_UNAVAILABLE",
            action="RETRY_LATER",
            reason="Feature configuration conflicts",
            feature=feature,
            jurisdiction=country,
            policy=policy,
            required_minimum_age=rules.minimum_age,
            required_assurance=rules.required_assurance_level,
        )
    if override is not None:
        feature_allowed = override
    if not feature_allowed:
        return _decision(
            allowed=False,
            code="FEATURE_UNAVAILABLE",
            action="CONTACT_SUPPORT",
            reason="Feature is unavailable in this jurisdiction",
            feature=feature,
            jurisdiction=country,
            policy=policy,
            required_minimum_age=rules.minimum_age,
            required_assurance=rules.required_assurance_level,
            feature_allowed=False,
        )

    if user is None and adult_restricted and not rules.anonymous_adult_preview_allowed:
        return _decision(
            allowed=False,
            code="ANONYMOUS_ADULT_PREVIEW_UNAVAILABLE",
            action="LOGIN",
            reason="Anonymous adult-restricted previews are unavailable in this jurisdiction",
            feature=feature,
            jurisdiction=country,
            policy=policy,
            required_minimum_age=rules.minimum_age,
            required_assurance=rules.required_assurance_level,
            feature_allowed=True,
        )

    age_required = (
        rules.fan_age_verification_required
        or adult_restricted
        or feature is ComplianceFeature.adult_media
    )
    verification = await _verification_state(
        db,
        user=user,
        anonymous_session_secret=anonymous_session_secret,
        country_code=country,
        now=current,
        required_minimum_age=rules.minimum_age,
        required_assurance_level=rules.required_assurance_level,
        reverify_after_days=rules.reverify_after_days,
        grace_period_days=rules.grace_period_days,
    )
    account_self_attested = has_current_self_attestation(user)
    if user is not None:
        # Never trust a caller-supplied boolean over the persisted current
        # account attestation version.
        legacy_self_attested = account_self_attested
    elif legacy_self_attested is None:
        legacy_self_attested = False
    effective_legacy_expiry = legacy_self_attested_expires_at
    if (
        user is not None
        and account_self_attested
        and user.adult_attested_at is not None
        and rules.reverify_after_days is not None
    ):
        account_policy_expiry = _now(user.adult_attested_at) + timedelta(
            days=rules.reverify_after_days
        )
        effective_legacy_expiry = (
            min(_now(effective_legacy_expiry), account_policy_expiry)
            if effective_legacy_expiry is not None
            else account_policy_expiry
        )
    if (
        verification.achieved_assurance_level is AgeAssuranceLevel.none
        and legacy_self_attested
        and rules.minimum_age <= 18
        and (effective_legacy_expiry is None or _now(effective_legacy_expiry) > current)
    ):
        verification = VerificationState(
            AgeVerificationStatus.verified,
            AgeAssuranceLevel.self_attested,
            18,
            country,
            effective_legacy_expiry,
        )
    if not age_required:
        return _decision(
            allowed=True,
            code="ALLOWED",
            action=None,
            reason="Policy allows access",
            feature=feature,
            jurisdiction=country,
            policy=policy,
            required_minimum_age=rules.minimum_age,
            required_assurance=AgeAssuranceLevel.none,
            verification=verification,
            age_access_allowed=True,
            feature_allowed=True,
        )

    assurance_ok = (
        ASSURANCE_STRENGTH[verification.achieved_assurance_level]
        >= ASSURANCE_STRENGTH[rules.required_assurance_level]
    )
    threshold_ok = (
        verification.achieved_minimum_age is not None
        and verification.achieved_minimum_age >= rules.minimum_age
    )
    if verification.status is AgeVerificationStatus.verified and assurance_ok and threshold_ok:
        return _decision(
            allowed=True,
            code="ALLOWED",
            action=None,
            reason="Policy and age-assurance requirements are satisfied",
            feature=feature,
            jurisdiction=country,
            policy=policy,
            required_minimum_age=rules.minimum_age,
            required_assurance=rules.required_assurance_level,
            verification=verification,
            age_access_allowed=True,
            feature_allowed=True,
        )
    if verification.status is AgeVerificationStatus.expired:
        code, action, reason = (
            "AGE_VERIFICATION_EXPIRED",
            "VERIFY_AGE",
            "Age verification has expired",
        )
    elif verification.status is AgeVerificationStatus.revoked:
        code, action, reason = (
            "AGE_VERIFICATION_REVOKED",
            "CONTACT_SUPPORT",
            "Age verification has been revoked",
        )
    elif verification.status is AgeVerificationStatus.review_required:
        code, action, reason = (
            "AGE_VERIFICATION_REQUIRED",
            "CONTACT_SUPPORT",
            "Age verification requires review",
        )
    elif verification.status is AgeVerificationStatus.verified:
        code, action, reason = (
            "AGE_ASSURANCE_INSUFFICIENT",
            "VERIFY_AGE",
            "Age-assurance result does not meet the current policy",
        )
    else:
        code, action, reason = (
            "AGE_VERIFICATION_REQUIRED",
            "VERIFY_AGE",
            "Age verification is required",
        )
    return _decision(
        allowed=False,
        code=code,
        action=action,
        reason=reason,
        feature=feature,
        jurisdiction=country,
        policy=policy,
        required_minimum_age=rules.minimum_age,
        required_assurance=rules.required_assurance_level,
        verification=verification,
        age_access_allowed=False,
        feature_allowed=True,
    )


async def production_policy_readiness(
    db: AsyncSession, *, settings: Settings | None = None, now: datetime | None = None
) -> tuple[bool, tuple[str, ...]]:
    """Database-aware readiness gate for production compliance authority."""

    settings = settings or get_settings()
    if settings.environment != "production":
        return True, ()
    current = _now(now)
    reasons: list[str] = []
    fallback_country = settings.effective_compliance_fallback_country()
    fallback_registry = (
        await db.get(CountryRegistry, fallback_country) if fallback_country else None
    )
    fallback_policy = (
        await effective_policy_for_country(db, fallback_country, now=current)
        if fallback_country and fallback_registry and fallback_registry.enabled
        else None
    )
    if fallback_policy is None:
        reasons.append("FALLBACK_JURISDICTION_NOT_READY")
    if await db.scalar(select(User.id).where(User.country_code.is_(None)).limit(1)) is not None:
        reasons.append("ACCOUNT_JURISDICTION_MIGRATION_REQUIRED")
    # Revisions are append-only and a later effective version supersedes an
    # earlier overlapping window. Readiness must inspect the same authoritative
    # winners as runtime; otherwise one old indefinite demo revision would make
    # a reviewed non-demo successor impossible to deploy.
    effective_template_revisions = (
        await db.scalars(
            select(CompliancePolicyTemplateRevision)
            .where(
                CompliancePolicyTemplateRevision.status.in_(PUBLISHED_STATUSES),
                CompliancePolicyTemplateRevision.effective_from <= current,
                or_(
                    CompliancePolicyTemplateRevision.effective_until.is_(None),
                    CompliancePolicyTemplateRevision.effective_until > current,
                ),
            )
            .order_by(
                CompliancePolicyTemplateRevision.template_id,
                CompliancePolicyTemplateRevision.version.desc(),
            )
            .distinct(CompliancePolicyTemplateRevision.template_id)
        )
    ).all()
    if any(revision.is_demo for revision in effective_template_revisions):
        reasons.append("ACTIVE_DEMO_TEMPLATE_POLICY")
    reviewed_policy = await db.scalar(
        select(JurisdictionPolicyRevision.id).where(
            JurisdictionPolicyRevision.status.in_(PUBLISHED_STATUSES),
            JurisdictionPolicyRevision.is_demo.is_(False),
            JurisdictionPolicyRevision.reviewed_at.is_not(None),
            JurisdictionPolicyRevision.effective_from <= current,
            or_(
                JurisdictionPolicyRevision.effective_until.is_(None),
                JurisdictionPolicyRevision.effective_until > current,
            ),
        )
    )
    if not reviewed_policy:
        reasons.append("NO_REVIEWED_EFFECTIVE_JURISDICTION_POLICY")
    effective_rows = (
        await db.execute(
            select(JurisdictionPolicyRevision, CompliancePolicyTemplateRevision)
            .join(
                CompliancePolicyTemplateRevision,
                CompliancePolicyTemplateRevision.id
                == JurisdictionPolicyRevision.template_revision_id,
            )
            .where(
                JurisdictionPolicyRevision.status.in_(PUBLISHED_STATUSES),
                JurisdictionPolicyRevision.reviewed_at.is_not(None),
                JurisdictionPolicyRevision.reviewed_by_user_id.is_not(None),
                JurisdictionPolicyRevision.effective_from <= current,
                or_(
                    JurisdictionPolicyRevision.effective_until.is_(None),
                    JurisdictionPolicyRevision.effective_until > current,
                ),
            )
            .order_by(
                JurisdictionPolicyRevision.country_code,
                JurisdictionPolicyRevision.version.desc(),
            )
            .distinct(JurisdictionPolicyRevision.country_code)
        )
    ).all()
    enabled_country_codes = (
        await db.scalars(
            select(CountryRegistry.code)
            .where(CountryRegistry.enabled.is_(True))
            .order_by(CountryRegistry.code)
        )
    ).all()
    for country_code in enabled_country_codes:
        if await effective_policy_for_country(db, country_code, now=current) is None:
            reasons.append("ENABLED_JURISDICTION_POLICY_MISSING")
            break
    for jurisdiction, template in effective_rows:
        if jurisdiction.is_demo and "ACTIVE_DEMO_JURISDICTION_POLICY" not in reasons:
            reasons.append("ACTIVE_DEMO_JURISDICTION_POLICY")
        if template.status not in PUBLISHED_STATUSES:
            if "ACTIVE_POLICY_TEMPLATE_NOT_PUBLISHED" not in reasons:
                reasons.append("ACTIVE_POLICY_TEMPLATE_NOT_PUBLISHED")
            continue
        if template.reviewed_at is None or template.reviewed_by_user_id is None:
            if "ACTIVE_POLICY_TEMPLATE_UNREVIEWED" not in reasons:
                reasons.append("ACTIVE_POLICY_TEMPLATE_UNREVIEWED")
            continue
        if template.effective_from > current or (
            template.effective_until is not None and template.effective_until <= current
        ):
            if "ACTIVE_POLICY_TEMPLATE_NOT_EFFECTIVE" not in reasons:
                reasons.append("ACTIVE_POLICY_TEMPLATE_NOT_EFFECTIVE")
            continue
        if template.is_demo:
            if "ACTIVE_DEMO_TEMPLATE_POLICY" not in reasons:
                reasons.append("ACTIVE_DEMO_TEMPLATE_POLICY")
            if "ACTIVE_POLICY_TEMPLATE_DEMO" not in reasons:
                reasons.append("ACTIVE_POLICY_TEMPLATE_DEMO")
        try:
            provider_rules = PolicyRules.model_validate(template.rules_json).model_dump()
            provider_overrides = PolicyOverrides.model_validate(jurisdiction.overrides_json)
            for field_name in provider_overrides.model_fields_set:
                provider_rules[field_name] = getattr(provider_overrides, field_name)
            effective_rules = PolicyRules.model_validate(provider_rules)
        except ValueError:
            reasons.append("ACTIVE_POLICY_RULES_INVALID")
            break
        selected_provider = effective_rules.age_provider
        if (
            selected_provider != settings.age_assurance_provider
            and "ACTIVE_POLICY_PROVIDER_MISMATCH" not in reasons
        ):
            reasons.append("ACTIVE_POLICY_PROVIDER_MISMATCH")
        # VerifyMyAge's factual normalized result contains no expiry. A policy
        # that requires fan verification must therefore supply a finite
        # re-verification interval; callback normalization persists that bound.
        if (
            selected_provider == "verifymyage"
            and effective_rules.fan_age_verification_required
            and effective_rules.reverify_after_days is None
            and "ACTIVE_POLICY_REVERIFY_REQUIRED" not in reasons
        ):
            reasons.append("ACTIVE_POLICY_REVERIFY_REQUIRED")
        # The selected legacy OAuth result proves only a Boolean fixed-18
        # threshold and does not expose the upstream verification method. It is
        # normalized conservatively as low assurance. Stronger or higher-age
        # policies need a method-bound adapter, not an optimistic inference.
        if selected_provider == "verifymyage" and effective_rules.fan_age_verification_required:
            if (
                ASSURANCE_STRENGTH[effective_rules.required_assurance_level]
                > ASSURANCE_STRENGTH[AgeAssuranceLevel.low]
                and "ACTIVE_POLICY_PROVIDER_ASSURANCE_UNSUPPORTED" not in reasons
            ):
                reasons.append("ACTIVE_POLICY_PROVIDER_ASSURANCE_UNSUPPORTED")
            if (
                effective_rules.minimum_age > 18
                and "ACTIVE_POLICY_PROVIDER_MINIMUM_AGE_UNSUPPORTED" not in reasons
            ):
                reasons.append("ACTIVE_POLICY_PROVIDER_MINIMUM_AGE_UNSUPPORTED")
    effective_feature_revisions = (
        await db.scalars(
            select(FeatureFlagRevision)
            .where(
                FeatureFlagRevision.effective_from <= current,
                or_(
                    FeatureFlagRevision.effective_until.is_(None),
                    FeatureFlagRevision.effective_until > current,
                ),
            )
            .order_by(
                FeatureFlagRevision.feature,
                FeatureFlagRevision.country_scope,
                FeatureFlagRevision.version.desc(),
            )
            .distinct(FeatureFlagRevision.feature, FeatureFlagRevision.country_scope)
        )
    ).all()
    if any(revision.is_demo for revision in effective_feature_revisions):
        reasons.append("ACTIVE_DEMO_FEATURE_FLAG")
    expected_callback = (
        f"{settings.api_origin.rstrip('/')}/api/v1/compliance/"
        f"age-verification/callback/{settings.age_assurance_provider}"
    )
    latest_probe = await db.scalar(
        select(AgeProviderProbe)
        .where(AgeProviderProbe.provider == settings.age_assurance_provider)
        .order_by(AgeProviderProbe.probed_at.desc())
        .limit(1)
    )
    if latest_probe is None:
        reasons.append("AGE_PROVIDER_NOT_PROBED")
    elif (
        latest_probe.status is not ProviderProbeStatus.healthy
        or not latest_probe.configuration_complete
        or latest_probe.callback_url != expected_callback
        or latest_probe.probed_at
        < current - timedelta(seconds=settings.age_provider_probe_max_age_seconds)
    ):
        reasons.append("AGE_PROVIDER_NOT_READY")
    return not reasons, tuple(reasons)
