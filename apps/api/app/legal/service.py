from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.compliance.locks import lock_compliance_subject
from app.core.config import Settings, get_settings
from app.models.identity import Role, User, UserRole
from app.models.legal import (
    LegalAcceptance,
    LegalAudience,
    LegalDocument,
    LegalDocumentStatus,
    LegalDocumentType,
    LegalDocumentVersion,
    SiteSettingsVersion,
)
from app.models.notification import NotificationIntent
from app.models.referral import AffiliatePartner, AffiliatePartnerStatus
from app.notifications.service import emit_transactional
from app.schemas.legal import (
    LegalAcceptanceResponse,
    LegalDocumentDetail,
    LegalDocumentPage,
    LegalDocumentResponse,
    LegalDocumentSummary,
    SiteSettingsResponse,
    SiteSocialLink,
)


class LegalError(ValueError):
    pass


REQUIRED_PRODUCTION_LEGAL_TYPES = (
    LegalDocumentType.terms,
    LegalDocumentType.privacy,
    LegalDocumentType.age_policy,
)

DRAFT_MUTABLE_FIELDS = frozenset(
    {
        "title",
        "body",
        "effective_from",
        "effective_until",
        "requires_acceptance",
        "requires_legal_review",
        "approved_for_publication",
        "is_demo",
    }
)
ACCEPTANCE_SOURCES = frozenset({"registration", "interstitial", "account"})


def _now() -> datetime:
    return datetime.now(UTC)


def _body_hash(body: list[dict]) -> str:
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


async def audiences_for_user(db: AsyncSession, user: User | None) -> tuple[LegalAudience, ...]:
    if user is None:
        return (LegalAudience.all_users,)
    roles = set(
        (
            await db.scalars(
                select(Role.name)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == user.id)
            )
        ).all()
    )
    audiences: list[LegalAudience] = []
    if "creator" in roles:
        audiences.append(LegalAudience.creator)
    if "manager" in roles:
        audiences.append(LegalAudience.group_manager)
    affiliate_owner = await db.scalar(
        select(AffiliatePartner.id).where(
            AffiliatePartner.owner_user_id == user.id,
            AffiliatePartner.status == AffiliatePartnerStatus.active,
        )
    )
    if affiliate_owner:
        audiences.append(LegalAudience.affiliate)
    if "viewer" in roles or not audiences:
        audiences.append(LegalAudience.fan)
    audiences.append(LegalAudience.all_users)
    return tuple(dict.fromkeys(audiences))


def document_version_response(
    document: LegalDocument, version: LegalDocumentVersion
) -> LegalDocumentResponse:
    return LegalDocumentResponse(
        document_id=document.id,
        version_id=version.id,
        document_type=document.document_type,
        title=version.title,
        slug=document.slug,
        jurisdiction_code=document.jurisdiction_code,
        language=document.language,
        audience=document.audience,
        version=version.version,
        status=version.status,
        body=version.body_json,
        effective_from=version.effective_from,
        effective_until=version.effective_until,
        requires_acceptance=version.requires_acceptance,
        requires_legal_review=version.requires_legal_review,
        approved_for_publication=version.approved_for_publication,
        is_demo=version.is_demo,
        published_at=version.published_at,
    )


def _version_summary(
    document: LegalDocument, version: LegalDocumentVersion
) -> LegalDocumentSummary:
    return LegalDocumentSummary(
        document_id=document.id,
        version_id=version.id,
        document_type=document.document_type,
        title=version.title,
        slug=document.slug,
        jurisdiction_code=document.jurisdiction_code,
        language=document.language,
        audience=document.audience,
        version=version.version,
        status=version.status,
        effective_from=version.effective_from,
        effective_until=version.effective_until,
        requires_acceptance=version.requires_acceptance,
        requires_legal_review=version.requires_legal_review,
        approved_for_publication=version.approved_for_publication,
        is_demo=version.is_demo,
        created_at=version.created_at,
        published_at=version.published_at,
    )


def _currently_effective(version: LegalDocumentVersion, now: datetime) -> bool:
    return bool(
        version.status is LegalDocumentStatus.published
        and version.published_at is not None
        and version.approved_for_publication
        and (version.effective_from is None or version.effective_from <= now)
        and (version.effective_until is None or version.effective_until > now)
        and not (get_settings().environment == "production" and version.is_demo)
    )


async def resolve_document(
    db: AsyncSession,
    slug: str,
    *,
    jurisdiction_code: str | None = None,
    language: str = "en",
    audiences: tuple[LegalAudience, ...] = (LegalAudience.all_users,),
    now: datetime | None = None,
) -> LegalDocumentResponse | None:
    """Resolve one effective version with country, language, and audience fallback."""

    current = now or _now()
    normalized_country = jurisdiction_code.upper() if jurisdiction_code else None
    rows = (
        await db.execute(
            select(LegalDocument, LegalDocumentVersion)
            .join(LegalDocumentVersion, LegalDocumentVersion.document_id == LegalDocument.id)
            .where(
                LegalDocument.slug == slug.lower(),
                LegalDocument.audience.in_(audiences),
                LegalDocument.language.in_(tuple(dict.fromkeys((language, "en")))),
                or_(
                    LegalDocument.jurisdiction_code.is_(None),
                    LegalDocument.jurisdiction_code == normalized_country,
                ),
                LegalDocumentVersion.status == LegalDocumentStatus.published,
            )
        )
    ).all()
    audience_rank = {
        audience: len(audiences) - position for position, audience in enumerate(audiences)
    }
    candidates = [
        (document, version) for document, version in rows if _currently_effective(version, current)
    ]
    if not candidates:
        return None
    document, version = max(
        candidates,
        key=lambda row: (
            int(normalized_country is not None and row[0].jurisdiction_code == normalized_country),
            int(row[0].language == language),
            audience_rank.get(row[0].audience, 0),
            row[1].version,
            row[1].effective_from or row[1].published_at or row[1].created_at,
            str(row[1].id),
        ),
    )
    return document_version_response(document, version)


async def active_documents(
    db: AsyncSession,
    *,
    jurisdiction_code: str | None = None,
    language: str = "en",
    audiences: tuple[LegalAudience, ...] = (LegalAudience.all_users,),
    now: datetime | None = None,
) -> list[LegalDocumentResponse]:
    slugs = (
        await db.scalars(
            select(LegalDocument.slug)
            .where(LegalDocument.audience.in_(audiences))
            .distinct()
            .order_by(LegalDocument.slug)
        )
    ).all()
    resolved = [
        await resolve_document(
            db,
            slug,
            jurisdiction_code=jurisdiction_code,
            language=language,
            audiences=audiences,
            now=now,
        )
        for slug in slugs
    ]
    return sorted(
        [document for document in resolved if document],
        key=lambda item: (item.document_type.value, item.title, item.slug),
    )


async def required_documents(
    db: AsyncSession,
    user: User | None,
    *,
    jurisdiction_code: str | None = None,
    language: str = "en",
    now: datetime | None = None,
) -> list[LegalDocumentResponse]:
    documents = await active_documents(
        db,
        jurisdiction_code=jurisdiction_code,
        language=language,
        audiences=await audiences_for_user(db, user),
        now=now,
    )
    required = [document for document in documents if document.requires_acceptance]
    if user is None or not required:
        return required
    accepted = set(
        (
            await db.scalars(
                select(LegalAcceptance.document_version_id).where(
                    LegalAcceptance.user_id == user.id,
                    LegalAcceptance.document_version_id.in_(
                        [document.version_id for document in required]
                    ),
                )
            )
        ).all()
    )
    return [document for document in required if document.version_id not in accepted]


async def has_effective_acceptance_requirements(
    db: AsyncSession,
    user: User,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether this account audience has any current acceptance obligation.

    This cheap preflight lets legacy/test accounts continue when no legal policy
    exists, while still failing closed on jurisdiction resolution as soon as an
    applicable published requirement exists.
    """

    current = now or _now()
    conditions = [
        LegalDocument.audience.in_(await audiences_for_user(db, user)),
        LegalDocumentVersion.status == LegalDocumentStatus.published,
        LegalDocumentVersion.published_at.is_not(None),
        LegalDocumentVersion.approved_for_publication.is_(True),
        LegalDocumentVersion.requires_acceptance.is_(True),
        or_(
            LegalDocumentVersion.effective_from.is_(None),
            LegalDocumentVersion.effective_from <= current,
        ),
        or_(
            LegalDocumentVersion.effective_until.is_(None),
            LegalDocumentVersion.effective_until > current,
        ),
    ]
    if get_settings().environment == "production":
        conditions.append(LegalDocumentVersion.is_demo.is_(False))
    return bool(
        await db.scalar(
            select(LegalDocumentVersion.id)
            .join(LegalDocument, LegalDocument.id == LegalDocumentVersion.document_id)
            .where(*conditions)
            .limit(1)
        )
    )


async def prospective_registration_requirements(
    db: AsyncSession,
    *,
    jurisdiction_code: str | None = None,
    language: str = "en",
    now: datetime | None = None,
) -> list[LegalDocumentResponse]:
    """Return the exact versions a prospective fan must accept before registration."""

    documents = await active_documents(
        db,
        jurisdiction_code=jurisdiction_code,
        language=language,
        audiences=(LegalAudience.fan, LegalAudience.all_users),
        now=now,
    )
    return [document for document in documents if document.requires_acceptance]


async def validate_registration_acceptances(
    db: AsyncSession,
    submitted_version_ids: list[UUID],
    *,
    jurisdiction_code: str,
    language: str = "en",
    now: datetime | None = None,
) -> list[LegalDocumentResponse]:
    """Validate an exact, no-more/no-less set for the prospective fan scope."""

    required = await prospective_registration_requirements(
        db,
        jurisdiction_code=jurisdiction_code,
        language=language,
        now=now,
    )
    submitted = set(submitted_version_ids)
    if len(submitted) != len(submitted_version_ids):
        raise LegalError("Legal document version IDs must be unique")
    expected = {document.version_id for document in required}
    if submitted != expected:
        raise LegalError("The current required legal document versions must be accepted")
    return required


async def record_registration_acceptances(
    db: AsyncSession,
    user: User,
    submitted_version_ids: list[UUID],
    *,
    jurisdiction_code: str,
    language: str = "en",
    correlation_id: str | None = None,
    now: datetime | None = None,
) -> list[LegalAcceptanceResponse]:
    """Validate and record registration acceptances in the caller's transaction."""

    required = await validate_registration_acceptances(
        db,
        submitted_version_ids,
        jurisdiction_code=jurisdiction_code,
        language=language,
        now=now,
    )
    return await record_acceptances(
        db,
        user,
        [document.version_id for document in required],
        source="registration",
        jurisdiction_code=jurisdiction_code,
        correlation_id=correlation_id,
        now=now,
    )


async def record_acceptances(
    db: AsyncSession,
    user: User,
    version_ids: list[UUID],
    *,
    source: str,
    jurisdiction_code: str,
    correlation_id: str | None,
    now: datetime | None = None,
) -> list[LegalAcceptanceResponse]:
    if source not in ACCEPTANCE_SOURCES:
        raise LegalError("Unsupported legal acceptance source")
    current = now or _now()
    # Acceptance is account-owned evidence. Serialize only this user and read
    # published versions without a global exclusive row lock: database
    # triggers already make published legal meaning immutable.
    await lock_compliance_subject(db, user.id)
    user_audiences = await audiences_for_user(db, user)
    allowed_audiences = set(user_audiences)
    output: list[LegalAcceptanceResponse] = []
    for version_id in sorted(set(version_ids), key=lambda value: value.int):
        row = await db.execute(
            select(LegalDocument, LegalDocumentVersion)
            .join(LegalDocumentVersion, LegalDocumentVersion.document_id == LegalDocument.id)
            .where(LegalDocumentVersion.id == version_id)
        )
        pair = row.one_or_none()
        if pair is None:
            raise LegalError("Legal document version not found")
        document, version = pair
        if not _currently_effective(version, current):
            raise LegalError("Legal document version is not currently available for acceptance")
        if not version.requires_acceptance:
            raise LegalError("Legal document version does not require acceptance")
        if document.audience not in allowed_audiences:
            raise LegalError("Legal document version does not apply to this account")
        if document.jurisdiction_code and document.jurisdiction_code != jurisdiction_code:
            raise LegalError("Legal document version does not apply to this jurisdiction")
        applicable = await resolve_document(
            db,
            document.slug,
            jurisdiction_code=jurisdiction_code,
            language=document.language,
            audiences=user_audiences,
            now=current,
        )
        if applicable is None or applicable.version_id != version.id:
            raise LegalError("A newer or more specific legal document version applies")
        acceptance = await db.scalar(
            select(LegalAcceptance).where(
                LegalAcceptance.user_id == user.id,
                LegalAcceptance.document_version_id == version.id,
            )
        )
        if not acceptance:
            acceptance = LegalAcceptance(
                user_id=user.id,
                document_version_id=version.id,
                accepted_at=current,
                source=source,
                jurisdiction_code=jurisdiction_code,
                correlation_id=correlation_id,
            )
            db.add(acceptance)
            await db.flush()
            await record_event(
                db,
                "legal.version_accepted",
                actor_user_id=user.id,
                target_type="legal_acceptance",
                target_id=str(acceptance.id),
                correlation_id=correlation_id,
                metadata={
                    "policy_type": document.document_type.value,
                    "policy_version": version.version,
                    "source": source,
                    "jurisdiction": jurisdiction_code,
                },
            )
        output.append(
            LegalAcceptanceResponse(
                acceptance_id=acceptance.id,
                version_id=version.id,
                document_type=document.document_type,
                title=version.title,
                version=version.version,
                jurisdiction_code=acceptance.jurisdiction_code,
                source=acceptance.source,
                accepted_at=acceptance.accepted_at,
            )
        )
    return output


async def acceptance_history(db: AsyncSession, user_id: UUID) -> list[LegalAcceptanceResponse]:
    rows = (
        await db.execute(
            select(LegalAcceptance, LegalDocumentVersion, LegalDocument)
            .join(
                LegalDocumentVersion,
                LegalDocumentVersion.id == LegalAcceptance.document_version_id,
            )
            .join(LegalDocument, LegalDocument.id == LegalDocumentVersion.document_id)
            .where(LegalAcceptance.user_id == user_id)
            .order_by(LegalAcceptance.accepted_at.desc(), LegalAcceptance.id.desc())
        )
    ).all()
    return [
        LegalAcceptanceResponse(
            acceptance_id=acceptance.id,
            version_id=version.id,
            document_type=document.document_type,
            title=version.title,
            version=version.version,
            jurisdiction_code=acceptance.jurisdiction_code,
            source=acceptance.source,
            accepted_at=acceptance.accepted_at,
        )
        for acceptance, version, document in rows
    ]


async def create_document(
    db: AsyncSession, actor: User, values: dict
) -> tuple[LegalDocument, LegalDocumentVersion]:
    jurisdiction_code = values["jurisdiction_code"]
    duplicate = await db.scalar(
        select(LegalDocument.id).where(
            LegalDocument.slug == values["slug"],
            LegalDocument.language == values["language"],
            LegalDocument.audience == values["audience"],
            LegalDocument.jurisdiction_code.is_(None)
            if jurisdiction_code is None
            else LegalDocument.jurisdiction_code == jurisdiction_code,
        )
    )
    if duplicate:
        raise LegalError("A legal document already exists for this scope")
    document = LegalDocument(
        document_type=values["document_type"],
        slug=values["slug"],
        jurisdiction_code=jurisdiction_code,
        language=values["language"],
        audience=values["audience"],
        created_by_user_id=actor.id,
    )
    db.add(document)
    await db.flush()
    version = LegalDocumentVersion(
        document_id=document.id,
        version=1,
        status=LegalDocumentStatus.draft,
        title=values["title"],
        body_json=values["body"],
        effective_from=values.get("effective_from"),
        effective_until=values.get("effective_until"),
        requires_acceptance=values.get("requires_acceptance", False),
        requires_legal_review=values.get("requires_legal_review", True),
        approved_for_publication=values.get("approved_for_publication", False),
        is_demo=values.get("is_demo", False),
        created_by_user_id=actor.id,
    )
    db.add(version)
    await db.flush()
    await record_event(
        db,
        "legal.draft_created",
        actor_user_id=actor.id,
        target_type="legal_document_version",
        target_id=str(version.id),
        metadata={
            "policy_type": document.document_type.value,
            "policy_version": version.version,
            "slug": document.slug,
            "jurisdiction": document.jurisdiction_code,
            "audience": document.audience.value,
        },
    )
    return document, version


async def create_version(
    db: AsyncSession, actor: User, document_id: UUID, values: dict
) -> tuple[LegalDocument, LegalDocumentVersion]:
    document = await db.scalar(
        select(LegalDocument).where(LegalDocument.id == document_id).with_for_update()
    )
    if not document:
        raise LegalError("Legal document not found")
    if await db.scalar(
        select(LegalDocumentVersion.id).where(
            LegalDocumentVersion.document_id == document.id,
            LegalDocumentVersion.status == LegalDocumentStatus.draft,
        )
    ):
        raise LegalError("Finish or retire the current draft before creating another version")
    previous = await db.scalar(
        select(func.max(LegalDocumentVersion.version)).where(
            LegalDocumentVersion.document_id == document.id
        )
    )
    version = LegalDocumentVersion(
        document_id=document.id,
        version=(previous or 0) + 1,
        status=LegalDocumentStatus.draft,
        title=values["title"],
        body_json=values["body"],
        effective_from=values.get("effective_from"),
        effective_until=values.get("effective_until"),
        requires_acceptance=values.get("requires_acceptance", False),
        requires_legal_review=values.get("requires_legal_review", True),
        approved_for_publication=values.get("approved_for_publication", False),
        is_demo=values.get("is_demo", False),
        created_by_user_id=actor.id,
    )
    db.add(version)
    await db.flush()
    await record_event(
        db,
        "legal.draft_created",
        actor_user_id=actor.id,
        target_type="legal_document_version",
        target_id=str(version.id),
        metadata={
            "policy_type": document.document_type.value,
            "policy_version": version.version,
            "slug": document.slug,
        },
    )
    return document, version


def _draft_snapshot(version: LegalDocumentVersion) -> dict[str, object]:
    return {
        "title": version.title,
        "body_hash": _body_hash(version.body_json),
        "effective_from": version.effective_from.isoformat() if version.effective_from else None,
        "effective_until": version.effective_until.isoformat() if version.effective_until else None,
        "requires_acceptance": version.requires_acceptance,
        "requires_legal_review": version.requires_legal_review,
        "approved_for_publication": version.approved_for_publication,
        "is_demo": version.is_demo,
    }


async def notify_required_legal_version(
    db: AsyncSession,
    version: LegalDocumentVersion,
    *,
    now: datetime | None = None,
    recipient_limit: int = 500,
) -> int:
    """Create a bounded, replay-safe fanout only while this version is applicable."""

    current = now or _now()
    if not version.requires_acceptance or not _currently_effective(version, current):
        return 0
    document = await db.get(LegalDocument, version.document_id)
    if document is None:
        return 0
    already_notified = select(NotificationIntent.recipient_user_id).where(
        NotificationIntent.notification_type == "LEGAL_ACCEPTANCE_REQUIRED",
        NotificationIntent.source_domain == "legal",
        NotificationIntent.source_id == str(version.id),
    )
    recipient_query = select(User).where(
        User.is_active.is_(True),
        User.email_verified_at.is_not(None),
        ~User.id.in_(already_notified),
    )
    if document.jurisdiction_code:
        recipient_query = recipient_query.where(User.country_code == document.jurisdiction_code)
    if document.audience is LegalAudience.fan:
        recipient_query = recipient_query.where(User.roles.any(Role.name == "viewer"))
    elif document.audience is LegalAudience.creator:
        recipient_query = recipient_query.where(User.roles.any(Role.name == "creator"))
    elif document.audience is LegalAudience.group_manager:
        recipient_query = recipient_query.where(User.roles.any(Role.name == "manager"))
    elif document.audience is LegalAudience.affiliate:
        recipient_query = recipient_query.where(
            User.id.in_(
                select(AffiliatePartner.owner_user_id).where(
                    AffiliatePartner.owner_user_id.is_not(None),
                    AffiliatePartner.status == AffiliatePartnerStatus.active,
                )
            )
        )
    candidates = (
        await db.scalars(recipient_query.order_by(User.id).limit(max(1, recipient_limit)))
    ).all()
    notified = 0
    for recipient in candidates:
        required = await required_documents(
            db,
            recipient,
            jurisdiction_code=recipient.country_code,
            now=current,
        )
        if version.id not in {item.version_id for item in required}:
            continue
        await emit_transactional(
            db,
            recipient_user_id=recipient.id,
            notification_type="LEGAL_ACCEPTANCE_REQUIRED",
            source_domain="legal",
            source_id=str(version.id),
            title="Updated terms require review",
            body=(
                "Review and accept the updated terms to continue using "
                "affected FanBackstage features."
            ),
            target_path="/account/legal",
            email=True,
        )
        notified += 1
    return notified


async def notify_due_legal_acceptance_versions(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    version_limit: int | None = None,
    recipient_limit: int = 500,
) -> int:
    """Fan out newly effective mandatory versions; safe to run repeatedly.

    The scheduled task intentionally scans every effective version. A fixed
    leading-version window can permanently starve later documents after the
    earlier versions have finished notifying their audiences. Callers may pass
    an explicit limit for bounded administrative probes; recipient fanout is
    independently capped and replay-safe for every exact version.
    """

    current = now or _now()
    await db.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended("legal-acceptance-notification-reconcile", 0)
            )
        )
    )
    query = (
        select(LegalDocumentVersion)
        .where(
            LegalDocumentVersion.status == LegalDocumentStatus.published,
            LegalDocumentVersion.published_at.is_not(None),
            LegalDocumentVersion.approved_for_publication.is_(True),
            LegalDocumentVersion.requires_acceptance.is_(True),
            or_(
                LegalDocumentVersion.effective_from.is_(None),
                LegalDocumentVersion.effective_from <= current,
            ),
            or_(
                LegalDocumentVersion.effective_until.is_(None),
                LegalDocumentVersion.effective_until > current,
            ),
        )
        .order_by(LegalDocumentVersion.effective_from, LegalDocumentVersion.id)
    )
    if version_limit is not None:
        query = query.limit(max(1, version_limit))
    versions = (await db.scalars(query)).all()
    notified = 0
    for version in versions:
        notified += await notify_required_legal_version(
            db,
            version,
            now=current,
            recipient_limit=recipient_limit,
        )
    return notified


async def update_draft(
    db: AsyncSession, actor: User, version_id: UUID, values: dict
) -> tuple[LegalDocument, LegalDocumentVersion]:
    version = await db.scalar(
        select(LegalDocumentVersion).where(LegalDocumentVersion.id == version_id).with_for_update()
    )
    if not version:
        raise LegalError("Legal document version not found")
    if version.status is not LegalDocumentStatus.draft:
        raise LegalError("Published and retired legal versions are immutable")
    unsupported = set(values).difference(DRAFT_MUTABLE_FIELDS)
    if unsupported:
        raise LegalError("Unsupported legal draft fields")
    before = _draft_snapshot(version)
    for field, value in values.items():
        setattr(version, "body_json" if field == "body" else field, value)
    if (
        version.effective_from
        and version.effective_until
        and version.effective_until <= version.effective_from
    ):
        raise LegalError("Legal effective-until must follow effective-from")
    after = _draft_snapshot(version)
    changes = [
        {"field": key, "before": before[key], "after": after[key]}
        for key in before
        if before[key] != after[key]
    ]
    document = await db.get(LegalDocument, version.document_id)
    assert document is not None
    if changes:
        await record_event(
            db,
            "legal.draft_updated",
            actor_user_id=actor.id,
            target_type="legal_document_version",
            target_id=str(version.id),
            metadata={"policy_version": version.version, "changes": changes},
        )
    return document, version


async def publish_version(
    db: AsyncSession,
    actor: User,
    version_id: UUID,
    *,
    reason: str,
    now: datetime | None = None,
) -> tuple[LegalDocument, LegalDocumentVersion]:
    version = await db.scalar(
        select(LegalDocumentVersion).where(LegalDocumentVersion.id == version_id).with_for_update()
    )
    if not version:
        raise LegalError("Legal document version not found")
    if version.status is not LegalDocumentStatus.draft:
        raise LegalError("Only a draft legal version can be published")
    if version.requires_legal_review or not version.approved_for_publication:
        raise LegalError("Legal review and publication approval are required")
    if get_settings().environment == "production" and version.is_demo:
        raise LegalError("Demo legal text cannot be published in production")
    current = now or _now()
    version.effective_from = version.effective_from or current
    if version.effective_until and version.effective_until <= version.effective_from:
        raise LegalError("Legal effective-until must follow effective-from")
    version.status = LegalDocumentStatus.published
    version.published_at = current
    version.published_by_user_id = actor.id
    document = await db.get(LegalDocument, version.document_id)
    assert document is not None
    notification_recipient_count = await notify_required_legal_version(
        db,
        version,
        now=current,
    )
    await record_event(
        db,
        "legal.version_published",
        actor_user_id=actor.id,
        target_type="legal_document_version",
        target_id=str(version.id),
        metadata={
            "policy_type": document.document_type.value,
            "policy_version": version.version,
            "slug": document.slug,
            "reason": reason,
            "effective_from": version.effective_from.isoformat(),
            "effective_until": version.effective_until.isoformat()
            if version.effective_until
            else None,
            "requires_acceptance": version.requires_acceptance,
            "notification_recipient_count": notification_recipient_count,
        },
    )
    return document, version


async def retire_version(
    db: AsyncSession,
    actor: User,
    version_id: UUID,
    *,
    reason: str,
    now: datetime | None = None,
) -> tuple[LegalDocument, LegalDocumentVersion]:
    version = await db.scalar(
        select(LegalDocumentVersion).where(LegalDocumentVersion.id == version_id).with_for_update()
    )
    if not version:
        raise LegalError("Legal document version not found")
    if version.status is LegalDocumentStatus.retired:
        document = await db.get(LegalDocument, version.document_id)
        assert document is not None
        return document, version
    if version.status is not LegalDocumentStatus.published:
        raise LegalError("Only a published legal version can be retired")
    version.status = LegalDocumentStatus.retired
    version.retired_at = now or _now()
    version.retired_by_user_id = actor.id
    document = await db.get(LegalDocument, version.document_id)
    assert document is not None
    await record_event(
        db,
        "legal.version_retired",
        actor_user_id=actor.id,
        target_type="legal_document_version",
        target_id=str(version.id),
        metadata={
            "policy_type": document.document_type.value,
            "policy_version": version.version,
            "reason": reason,
        },
    )
    return document, version


async def document_detail(db: AsyncSession, document_id: UUID) -> LegalDocumentDetail:
    document = await db.get(LegalDocument, document_id)
    if not document:
        raise LegalError("Legal document not found")
    versions = (
        await db.scalars(
            select(LegalDocumentVersion)
            .where(LegalDocumentVersion.document_id == document.id)
            .order_by(LegalDocumentVersion.version.desc())
        )
    ).all()
    return LegalDocumentDetail(
        document_id=document.id,
        document_type=document.document_type,
        slug=document.slug,
        jurisdiction_code=document.jurisdiction_code,
        language=document.language,
        audience=document.audience,
        versions=[_version_summary(document, version) for version in versions],
    )


async def version_detail(
    db: AsyncSession, version_id: UUID
) -> tuple[LegalDocument, LegalDocumentVersion]:
    row = await db.execute(
        select(LegalDocument, LegalDocumentVersion)
        .join(LegalDocumentVersion, LegalDocumentVersion.document_id == LegalDocument.id)
        .where(LegalDocumentVersion.id == version_id)
    )
    pair = row.one_or_none()
    if pair is None:
        raise LegalError("Legal document version not found")
    return pair


async def list_documents(
    db: AsyncSession,
    *,
    search: str | None = None,
    status: LegalDocumentStatus | None = None,
    document_type: LegalDocumentType | None = None,
    jurisdiction_code: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> LegalDocumentPage:
    conditions = []
    if search:
        pattern = f"%{search.strip()}%"
        conditions.append(
            or_(
                LegalDocument.slug.ilike(pattern),
                LegalDocumentVersion.title.ilike(pattern),
            )
        )
    if status:
        conditions.append(LegalDocumentVersion.status == status)
    if document_type:
        conditions.append(LegalDocument.document_type == document_type)
    if jurisdiction_code:
        conditions.append(LegalDocument.jurisdiction_code == jurisdiction_code.upper())
    query = select(LegalDocument, LegalDocumentVersion).join(
        LegalDocumentVersion, LegalDocumentVersion.document_id == LegalDocument.id
    )
    count_query = (
        select(func.count(LegalDocumentVersion.id))
        .select_from(LegalDocumentVersion)
        .join(LegalDocument, LegalDocument.id == LegalDocumentVersion.document_id)
    )
    if conditions:
        query = query.where(and_(*conditions))
        count_query = count_query.where(and_(*conditions))
    rows = (
        await db.execute(
            query.order_by(
                LegalDocument.updated_at.desc(),
                LegalDocument.slug,
                LegalDocumentVersion.version.desc(),
            )
            .offset(max(offset, 0))
            .limit(min(max(limit, 1), 100))
        )
    ).all()
    total = int(await db.scalar(count_query) or 0)
    return LegalDocumentPage(
        items=[_version_summary(document, version) for document, version in rows],
        total=total,
        limit=min(max(limit, 1), 100),
        offset=max(offset, 0),
    )


def _settings_response(
    settings: SiteSettingsVersion | None, *, now: datetime | None = None
) -> SiteSettingsResponse:
    current = now or _now()
    if settings is None:
        return SiteSettingsResponse(
            version=0,
            support_email=None,
            footer_text=None,
            public_contact_text=None,
            social_links=[],
            homepage_announcement=None,
            maintenance_notice=None,
            banner_level="info",
            banner_starts_at=None,
            banner_ends_at=None,
            banner_active=False,
            updated_at=None,
        )
    has_banner = bool(settings.maintenance_notice or settings.homepage_announcement)
    active = bool(
        has_banner
        and (settings.banner_starts_at is None or settings.banner_starts_at <= current)
        and (settings.banner_ends_at is None or settings.banner_ends_at > current)
    )
    return SiteSettingsResponse(
        version=settings.version,
        support_email=settings.support_email,
        footer_text=settings.footer_text,
        public_contact_text=settings.public_contact_text,
        social_links=[SiteSocialLink.model_validate(item) for item in settings.social_links_json],
        homepage_announcement=settings.homepage_announcement,
        maintenance_notice=settings.maintenance_notice,
        banner_level=settings.banner_level,
        banner_starts_at=settings.banner_starts_at,
        banner_ends_at=settings.banner_ends_at,
        banner_active=active,
        updated_at=settings.updated_at,
    )


async def current_site_settings(
    db: AsyncSession, *, now: datetime | None = None
) -> SiteSettingsResponse:
    settings = await db.scalar(
        select(SiteSettingsVersion)
        .where(SiteSettingsVersion.is_current.is_(True))
        .order_by(SiteSettingsVersion.version.desc())
    )
    return _settings_response(settings, now=now)


async def update_site_settings(db: AsyncSession, actor: User, values: dict) -> SiteSettingsResponse:
    """Serialize append-only singleton updates with a PostgreSQL transaction lock."""

    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": 4_621_917_723})
    previous = await db.scalar(
        select(SiteSettingsVersion)
        .where(SiteSettingsVersion.is_current.is_(True))
        .order_by(SiteSettingsVersion.version.desc())
        .with_for_update()
    )
    before = _settings_response(previous).model_dump(mode="json")
    if previous:
        previous.is_current = False
        await db.flush()
    row = SiteSettingsVersion(
        version=(previous.version if previous else 0) + 1,
        is_current=True,
        support_email=str(values["support_email"]) if values.get("support_email") else None,
        footer_text=values.get("footer_text"),
        public_contact_text=values.get("public_contact_text"),
        social_links_json=values.get("social_links", []),
        homepage_announcement=values.get("homepage_announcement"),
        maintenance_notice=values.get("maintenance_notice"),
        banner_level=values.get("banner_level", "info"),
        banner_starts_at=values.get("banner_starts_at"),
        banner_ends_at=values.get("banner_ends_at"),
        updated_by_user_id=actor.id,
        reason=values["reason"],
    )
    db.add(row)
    await db.flush()
    after = _settings_response(row).model_dump(mode="json")
    excluded = {"version", "updated_at", "banner_active"}
    changes = [
        {"field": key, "before": before.get(key), "after": after.get(key)}
        for key in after
        if key not in excluded and before.get(key) != after.get(key)
    ]
    await record_event(
        db,
        "site_settings.version_created",
        actor_user_id=actor.id,
        target_type="site_settings_version",
        target_id=str(row.id),
        metadata={"version": row.version, "reason": row.reason, "changes": changes},
    )
    return _settings_response(row)


async def production_legal_readiness(
    db: AsyncSession,
    *,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> tuple[bool, tuple[str, ...]]:
    """Detect unsafe production legal state without treating demo text as authority."""

    settings = settings or get_settings()
    if settings.environment != "production":
        return True, ()
    current = now or _now()
    effective_window = and_(
        or_(
            LegalDocumentVersion.effective_from.is_(None),
            LegalDocumentVersion.effective_from <= current,
        ),
        or_(
            LegalDocumentVersion.effective_until.is_(None),
            LegalDocumentVersion.effective_until > current,
        ),
    )
    reasons: list[str] = []
    active_demo = await db.scalar(
        select(LegalDocumentVersion.id).where(
            LegalDocumentVersion.status == LegalDocumentStatus.published,
            LegalDocumentVersion.published_at.is_not(None),
            LegalDocumentVersion.is_demo.is_(True),
            effective_window,
        )
    )
    if active_demo:
        reasons.append("ACTIVE_DEMO_LEGAL_VERSION")

    ready_types = set(
        (
            await db.scalars(
                select(LegalDocument.document_type)
                .join(
                    LegalDocumentVersion,
                    LegalDocumentVersion.document_id == LegalDocument.id,
                )
                .where(
                    LegalDocument.document_type.in_(REQUIRED_PRODUCTION_LEGAL_TYPES),
                    LegalDocument.jurisdiction_code.is_(None),
                    LegalDocument.language == "en",
                    LegalDocument.audience.in_((LegalAudience.all_users, LegalAudience.fan)),
                    LegalDocumentVersion.status == LegalDocumentStatus.published,
                    LegalDocumentVersion.published_at.is_not(None),
                    LegalDocumentVersion.approved_for_publication.is_(True),
                    LegalDocumentVersion.requires_legal_review.is_(False),
                    LegalDocumentVersion.is_demo.is_(False),
                    effective_window,
                )
                .distinct()
            )
        ).all()
    )
    for document_type in REQUIRED_PRODUCTION_LEGAL_TYPES:
        if document_type not in ready_types:
            reasons.append(f"MISSING_REQUIRED_LEGAL_{document_type.value.upper()}")
    return not reasons, tuple(reasons)
