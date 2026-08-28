from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.compliance.locks import lock_compliance_subject
from app.compliance.policy import (
    effective_policy_for_country,
    policy_for_revision,
    resolve_compliance_decision,
)
from app.compliance.types import normalize_country_code
from app.core.config import Settings, get_settings
from app.integrations.age_verification import (
    ProviderDiagnostic,
    ProviderError,
    ProviderStartRequest,
    get_age_verification_provider,
)
from app.models.compliance import (
    AgeAssuranceLevel,
    AgeProviderCallbackEvent,
    AgeProviderProbe,
    AgeVerificationRecord,
    AgeVerificationStatus,
    AnonymousComplianceSession,
    ComplianceFeature,
    CountryRegistry,
    ProviderCallbackStatus,
    ProviderProbeStatus,
)
from app.models.identity import User
from app.notifications.service import emit_transactional


class AgeVerificationError(ValueError):
    def __init__(self, message: str, *, code: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True)
class AgeVerificationStart:
    record: AgeVerificationRecord
    authorization_url: str
    anonymous_session_secret: str | None
    anonymous_session_expires_at: datetime | None


@dataclass(frozen=True)
class AgeVerificationCompletion:
    record: AgeVerificationRecord
    safe_return_path: str
    replayed: bool
    anonymous_session_expires_at: datetime | None


def _now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    return current if current.tzinfo else current.replace(tzinfo=UTC)


def _digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


async def _reject_normalized_outcome(
    db: AsyncSession,
    *,
    record: AgeVerificationRecord,
    event: AgeProviderCallbackEvent,
    provider_name: str,
    reason_code: str,
    message: str,
    current: datetime,
) -> None:
    event.status = ProviderCallbackStatus.rejected
    event.failure_reason_code = reason_code
    event.processed_at = current
    record.status = AgeVerificationStatus.review_required
    record.state_consumed_at = current
    record.failure_reason_code = reason_code
    record.retryable = False
    await record_event(
        db,
        "compliance.age_verification_callback_failed",
        actor_user_id=record.user_id,
        target_type="age_verification_record",
        target_id=str(record.id),
        metadata={
            "provider": provider_name,
            "reason_code": reason_code,
            "retryable": False,
        },
    )
    if record.user_id:
        await emit_transactional(
            db,
            recipient_user_id=record.user_id,
            notification_type="AGE_VERIFICATION_ACTION_REQUIRED",
            source_domain="compliance",
            source_id=str(record.id),
            title="Age verification needs attention",
            body=(
                "Your age verification needs attention before restricted features are available."
            ),
            target_path="/account",
            email=True,
        )
    raise AgeVerificationError(message, code=reason_code, retryable=False)


def safe_internal_return_path(value: str | None) -> str:
    path = (value or "/").strip()
    if (
        not path.startswith("/")
        or path.startswith("//")
        or "\\" in path
        or len(path) > 512
        or any(ord(character) < 32 for character in path)
    ):
        raise AgeVerificationError("Return path must be internal", code="UNSAFE_RETURN_PATH")
    parsed = urlsplit(path)
    if parsed.scheme or parsed.netloc:
        raise AgeVerificationError("Return path must be internal", code="UNSAFE_RETURN_PATH")
    return path


async def create_anonymous_compliance_session(
    db: AsyncSession, *, now: datetime | None = None, settings: Settings | None = None
) -> tuple[AnonymousComplianceSession, str]:
    settings = settings or get_settings()
    current = _now(now)
    raw_secret = secrets.token_urlsafe(32)
    session = AnonymousComplianceSession(
        secret_hash=_digest(raw_secret),
        expires_at=current + timedelta(hours=settings.anonymous_compliance_session_ttl_hours),
    )
    db.add(session)
    await db.flush()
    return session, raw_secret


async def _anonymous_session_for_secret(
    db: AsyncSession,
    secret: str,
    *,
    now: datetime,
    lock: bool = False,
) -> AnonymousComplianceSession | None:
    query = select(AnonymousComplianceSession).where(
        AnonymousComplianceSession.secret_hash == _digest(secret),
        AnonymousComplianceSession.revoked_at.is_(None),
        AnonymousComplianceSession.expires_at > now,
    )
    if lock:
        query = query.with_for_update()
    return await db.scalar(query)


async def start_age_verification(
    db: AsyncSession,
    *,
    user: User | None,
    country_code: str,
    safe_return_path: str = "/",
    anonymous_session_secret: str | None = None,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> AgeVerificationStart:
    settings = settings or get_settings()
    current = _now(now)
    country = normalize_country_code(country_code)
    assert country is not None
    registry = await db.get(CountryRegistry, country)
    if registry is None or not registry.enabled:
        raise AgeVerificationError(
            "Age verification is unavailable in this jurisdiction",
            code="JURISDICTION_BLOCKED",
        )
    policy = await effective_policy_for_country(db, country, now=current)
    if policy is None or not policy.rules.enabled:
        raise AgeVerificationError(
            "No effective age policy is available", code="POLICY_UNAVAILABLE"
        )
    session: AnonymousComplianceSession | None = None
    raw_anonymous_secret: str | None = None
    if user is None:
        if anonymous_session_secret:
            session = await _anonymous_session_for_secret(
                db, anonymous_session_secret, now=current, lock=True
            )
        if session is None:
            session, raw_anonymous_secret = await create_anonymous_compliance_session(
                db, now=current, settings=settings
            )
        else:
            raw_anonymous_secret = anonymous_session_secret
    state = secrets.token_urlsafe(32)
    return_path = safe_internal_return_path(safe_return_path)
    record = AgeVerificationRecord(
        user_id=user.id if user else None,
        anonymous_session_id=session.id if session else None,
        provider=policy.rules.age_provider,
        state_hash=_digest(state),
        safe_return_path=return_path,
        country_code=country,
        applicable_policy_id=policy.jurisdiction_revision.id,
        applicable_policy_version=policy.jurisdiction_revision.version,
        required_minimum_age=policy.rules.minimum_age,
        required_assurance_level=policy.rules.required_assurance_level,
        achieved_assurance_level=AgeAssuranceLevel.none,
        status=AgeVerificationStatus.pending,
        initiated_at=current,
    )
    db.add(record)
    await db.flush()
    try:
        provider = get_age_verification_provider(record.provider, settings=settings)
        started = await provider.create_verification_session(
            ProviderStartRequest(
                country_code=country,
                state=state,
                redirect_uri=(
                    f"{settings.api_origin.rstrip('/')}/api/v1/compliance/"
                    f"age-verification/callback/{record.provider}"
                ),
                user_reference=str(user.id) if user else None,
            )
        )
    except ProviderError as exc:
        record.status = AgeVerificationStatus.failed
        record.failed_at = current
        record.failure_reason_code = exc.code
        record.retryable = exc.retryable
        if not exc.retryable:
            record.status = AgeVerificationStatus.failed
            record.failed_at = current
            record.state_consumed_at = current
        await record_event(
            db,
            "compliance.age_verification_start_failed",
            actor_user_id=user.id if user else None,
            target_type="age_verification_record",
            target_id=str(record.id),
            metadata={
                "provider": record.provider,
                "country_code": country,
                "reason_code": exc.code,
                "retryable": exc.retryable,
            },
        )
        if user is not None:
            await emit_transactional(
                db,
                recipient_user_id=user.id,
                notification_type="AGE_VERIFICATION_ACTION_REQUIRED",
                source_domain="compliance",
                source_id=str(record.id),
                title="Age verification needs attention",
                body=("Age verification could not start. Review the next steps in your account."),
                target_path="/account",
                email=True,
            )
        raise AgeVerificationError(str(exc), code=exc.code, retryable=exc.retryable) from exc
    await record_event(
        db,
        "compliance.age_verification_started",
        actor_user_id=user.id if user else None,
        target_type="age_verification_record",
        target_id=str(record.id),
        metadata={
            "provider": record.provider,
            "country_code": country,
            "policy_version": record.applicable_policy_version,
            "required_assurance": record.required_assurance_level.value,
        },
    )
    return AgeVerificationStart(
        record=record,
        authorization_url=started.authorization_url,
        anonymous_session_secret=raw_anonymous_secret,
        anonymous_session_expires_at=session.expires_at if session else None,
    )


async def complete_browser_callback(
    db: AsyncSession,
    *,
    provider_name: str,
    state: str,
    code: str,
    now: datetime | None = None,
    settings: Settings | None = None,
) -> AgeVerificationCompletion:
    settings = settings or get_settings()
    current = _now(now)
    if not state or not code:
        raise AgeVerificationError("Provider callback is incomplete", code="CALLBACK_INCOMPLETE")
    state_hash = _digest(state)
    external_event_id = _digest(f"browser:{provider_name}:{state}:{code}")
    record = await db.scalar(
        select(AgeVerificationRecord)
        .where(
            AgeVerificationRecord.state_hash == state_hash,
            AgeVerificationRecord.provider == provider_name,
        )
        .with_for_update()
    )
    if record is None:
        raise AgeVerificationError("Provider callback state is invalid", code="STATE_INVALID")
    if record.user_id is not None:
        await lock_compliance_subject(db, record.user_id)
    # The record lock serializes duplicate deliveries. Re-check the event only
    # after acquiring it so a concurrent loser returns the canonical outcome.
    existing_event = await db.scalar(
        select(AgeProviderCallbackEvent)
        .where(
            AgeProviderCallbackEvent.provider == provider_name,
            AgeProviderCallbackEvent.external_event_id == external_event_id,
        )
        .with_for_update()
    )
    if existing_event and existing_event.status is ProviderCallbackStatus.processed:
        session = (
            await db.get(AnonymousComplianceSession, record.anonymous_session_id)
            if record.anonymous_session_id
            else None
        )
        return AgeVerificationCompletion(
            record, record.safe_return_path, True, session.expires_at if session else None
        )
    if (
        existing_event
        and existing_event.status is ProviderCallbackStatus.rejected
        and record.state_consumed_at is not None
    ):
        raise AgeVerificationError(
            "Provider callback was rejected",
            code=existing_event.failure_reason_code or "PROVIDER_CALLBACK_REJECTED",
            retryable=False,
        )
    if record.state_consumed_at is not None:
        raise AgeVerificationError(
            "Provider callback state was already used", code="STATE_REPLAYED"
        )
    event = existing_event or AgeProviderCallbackEvent(
        provider=provider_name,
        external_event_id=external_event_id,
        verification_record_id=record.id,
        status=ProviderCallbackStatus.received,
    )
    db.add(event)
    session: AnonymousComplianceSession | None = None
    if record.anonymous_session_id:
        session = await db.scalar(
            select(AnonymousComplianceSession)
            .where(AnonymousComplianceSession.id == record.anonymous_session_id)
            .with_for_update()
        )
    state_expires_at = _now(record.initiated_at) + timedelta(
        hours=settings.anonymous_compliance_session_ttl_hours
    )
    stale_code: str | None = None
    if state_expires_at <= current:
        stale_code = "CALLBACK_STATE_EXPIRED"
    elif record.anonymous_session_id and (
        session is None or session.revoked_at is not None or session.expires_at <= current
    ):
        stale_code = "ANONYMOUS_SESSION_EXPIRED"
    if stale_code is not None:
        event.status = ProviderCallbackStatus.rejected
        event.failure_reason_code = stale_code
        event.processed_at = current
        record.status = AgeVerificationStatus.failed
        record.failed_at = current
        record.failure_reason_code = stale_code
        record.retryable = False
        record.state_consumed_at = current
        await record_event(
            db,
            "compliance.age_verification_callback_expired",
            actor_user_id=record.user_id,
            target_type="age_verification_record",
            target_id=str(record.id),
            metadata={"provider": provider_name, "reason_code": stale_code},
        )
        if record.user_id:
            await emit_transactional(
                db,
                recipient_user_id=record.user_id,
                notification_type="AGE_VERIFICATION_ACTION_REQUIRED",
                source_domain="compliance",
                source_id=str(record.id),
                title="Age verification needs attention",
                body="Your age verification link expired. Start a new verification.",
                target_path="/account",
                email=True,
            )
        raise AgeVerificationError(
            "Provider callback state expired",
            code=stale_code,
            retryable=False,
        )
    try:
        provider = get_age_verification_provider(provider_name, settings=settings)
        outcome = await provider.exchange_browser_callback(code)
    except ProviderError as exc:
        event.status = ProviderCallbackStatus.rejected
        event.failure_reason_code = exc.code
        event.processed_at = current
        record.failure_reason_code = exc.code
        record.retryable = exc.retryable
        if not exc.retryable:
            record.status = AgeVerificationStatus.failed
            record.failed_at = current
            record.state_consumed_at = current
        await record_event(
            db,
            "compliance.age_verification_callback_failed",
            actor_user_id=record.user_id,
            target_type="age_verification_record",
            target_id=str(record.id),
            metadata={
                "provider": provider_name,
                "reason_code": exc.code,
                "retryable": exc.retryable,
            },
        )
        if record.user_id and not exc.retryable:
            await emit_transactional(
                db,
                recipient_user_id=record.user_id,
                notification_type="AGE_VERIFICATION_ACTION_REQUIRED",
                source_domain="compliance",
                source_id=str(record.id),
                title="Age verification needs attention",
                body=(
                    "Your age verification needs attention before restricted features "
                    "are available."
                ),
                target_path="/account",
                email=True,
            )
        raise AgeVerificationError(str(exc), code=exc.code, retryable=exc.retryable) from exc

    provider_reference = outcome.provider_verification_id
    if (
        not isinstance(provider_reference, str)
        or not provider_reference.strip()
        or len(provider_reference.strip()) > 255
    ):
        await _reject_normalized_outcome(
            db,
            record=record,
            event=event,
            provider_name=provider_name,
            reason_code="NORMALIZED_PROVIDER_REFERENCE_INVALID",
            message="Provider verification reference is invalid",
            current=current,
        )
    assert isinstance(provider_reference, str)
    provider_reference = provider_reference.strip()
    # Serialize by normalized provider reference before checking the unique
    # anti-transfer invariant. This avoids a race where two independent states
    # both miss the row and one later leaks an IntegrityError at commit.
    await db.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(
                    f"age-provider-reference:{provider_name}:{provider_reference}", 0
                )
            )
        )
    )
    existing_reference = await db.scalar(
        select(AgeVerificationRecord)
        .where(
            AgeVerificationRecord.provider == provider_name,
            AgeVerificationRecord.provider_verification_id == provider_reference,
            AgeVerificationRecord.id != record.id,
        )
        .with_for_update()
    )
    if existing_reference is not None:
        await _reject_normalized_outcome(
            db,
            record=record,
            event=event,
            provider_name=provider_name,
            reason_code="PROVIDER_REFERENCE_REUSED",
            message="Provider verification reference was already used",
            current=current,
        )

    applicable_policy = (
        await policy_for_revision(db, record.applicable_policy_id)
        if outcome.status is AgeVerificationStatus.verified
        else None
    )
    record.state_consumed_at = current
    record.provider_verification_id = provider_reference
    record.achieved_assurance_level = outcome.achieved_assurance_level
    record.achieved_minimum_age = outcome.achieved_minimum_age
    record.status = outcome.status
    record.retryable = outcome.retryable
    record.failure_reason_code = outcome.failure_reason_code
    record.result_metadata_json = {
        "age_verified": outcome.age_verified,
        "threshold": outcome.achieved_minimum_age,
    }
    if outcome.status is AgeVerificationStatus.verified:
        if (
            not outcome.age_verified
            or outcome.achieved_assurance_level is AgeAssuranceLevel.none
            or outcome.achieved_minimum_age is None
        ):
            record.status = AgeVerificationStatus.review_required
            record.failure_reason_code = "NORMALIZED_RESULT_INCOMPLETE"
        else:
            verified_at = _now(outcome.verified_at or current)
            provider_expiry = _now(outcome.expires_at) if outcome.expires_at else None
            policy_expiry = (
                verified_at + timedelta(days=applicable_policy.rules.reverify_after_days)
                if applicable_policy and applicable_policy.rules.reverify_after_days is not None
                else None
            )
            policy_terminal_expiry = (
                policy_expiry + timedelta(days=applicable_policy.rules.grace_period_days)
                if policy_expiry is not None and applicable_policy is not None
                else None
            )
            validity_bounds = tuple(
                expiry for expiry in (provider_expiry, policy_terminal_expiry) if expiry is not None
            )
            effective_expiry = min(validity_bounds) if validity_bounds else None
            record.result_metadata_json = {
                **record.result_metadata_json,
                "provider_expires_at": (
                    provider_expiry.isoformat() if provider_expiry else "not_provided"
                ),
            }
            if (
                verified_at > datetime.now(UTC) + timedelta(minutes=5)
                or effective_expiry is None
                or effective_expiry <= verified_at
            ):
                record.status = AgeVerificationStatus.review_required
                record.failure_reason_code = "NORMALIZED_VALIDITY_INCOMPLETE"
                record.verified_at = None
                record.expires_at = None
            else:
                record.verified_at = verified_at
                record.expires_at = effective_expiry
                if effective_expiry <= current:
                    record.status = AgeVerificationStatus.expired
                    record.failure_reason_code = "VERIFICATION_EXPIRED"
    elif outcome.status is AgeVerificationStatus.failed:
        record.failed_at = current
    if record.anonymous_session_id:
        if session is None:
            session = await db.scalar(
                select(AnonymousComplianceSession)
                .where(AnonymousComplianceSession.id == record.anonymous_session_id)
                .with_for_update()
            )
        if session is not None:
            terminal_expiry = (
                current
                if record.status
                in {
                    AgeVerificationStatus.expired,
                    AgeVerificationStatus.revoked,
                }
                else (record.expires_at)
            )
            if terminal_expiry is not None and terminal_expiry < session.expires_at:
                session.expires_at = terminal_expiry
    event.status = ProviderCallbackStatus.processed
    event.processed_at = current
    await record_event(
        db,
        "compliance.age_verification_completed",
        actor_user_id=record.user_id,
        target_type="age_verification_record",
        target_id=str(record.id),
        metadata={
            "provider": provider_name,
            "status": record.status.value,
            "achieved_assurance": record.achieved_assurance_level.value,
            "achieved_minimum_age": record.achieved_minimum_age,
        },
    )
    if record.user_id:
        verified = record.status is AgeVerificationStatus.verified
        await emit_transactional(
            db,
            recipient_user_id=record.user_id,
            notification_type=(
                "AGE_VERIFICATION_COMPLETED" if verified else "AGE_VERIFICATION_ACTION_REQUIRED"
            ),
            source_domain="compliance",
            source_id=str(record.id),
            title="Age verification completed" if verified else "Age verification needs attention",
            body=(
                "Your age verification is complete."
                if verified
                else "Your age verification needs attention before restricted features are available."
            ),
            target_path="/account",
            email=True,
        )
    return AgeVerificationCompletion(
        record,
        record.safe_return_path,
        False,
        session.expires_at if session else None,
    )


async def attach_anonymous_session(
    db: AsyncSession,
    *,
    anonymous_session_secret: str,
    user: User,
    now: datetime | None = None,
) -> AnonymousComplianceSession:
    current = _now(now)
    session = await _anonymous_session_for_secret(
        db, anonymous_session_secret, now=current, lock=True
    )
    if session is None:
        raise AgeVerificationError(
            "Anonymous compliance session is invalid", code="SESSION_INVALID"
        )
    if session.attached_user_id is not None and session.attached_user_id != user.id:
        raise AgeVerificationError(
            "Anonymous compliance session is already attached", code="SESSION_ALREADY_ATTACHED"
        )
    records = (
        await db.scalars(
            select(AgeVerificationRecord)
            .where(AgeVerificationRecord.anonymous_session_id == session.id)
            .with_for_update()
        )
    ).all()
    if any(record.user_id is not None and record.user_id != user.id for record in records):
        raise AgeVerificationError(
            "Anonymous verification subject cannot be reassigned", code="SUBJECT_REASSIGNMENT"
        )
    if session.attached_user_id is None:
        session.attached_user_id = user.id
        session.attached_at = current
        # The database trigger verifies that each record is attached only to
        # the same user as its locked anonymous session.
        await db.flush()
        for record in records:
            record.user_id = user.id
        await record_event(
            db,
            "compliance.anonymous_session_attached",
            actor_user_id=user.id,
            target_type="anonymous_compliance_session",
            target_id=str(session.id),
            metadata={"verification_count": len(records)},
        )
    return session


async def latest_age_verification(
    db: AsyncSession,
    *,
    user: User | None,
    anonymous_session_secret: str | None,
    now: datetime | None = None,
) -> AgeVerificationRecord | None:
    current = _now(now)
    subject_filters = []
    if user is not None:
        subject_filters.append(AgeVerificationRecord.user_id == user.id)
    if anonymous_session_secret:
        session = await _anonymous_session_for_secret(db, anonymous_session_secret, now=current)
        if session is not None and (
            session.attached_user_id is None
            or (user is not None and session.attached_user_id == user.id)
        ):
            subject_filters.append(AgeVerificationRecord.anonymous_session_id == session.id)
    if not subject_filters:
        return None
    return await db.scalar(
        select(AgeVerificationRecord)
        .where(or_(*subject_filters))
        .order_by(AgeVerificationRecord.created_at.desc(), AgeVerificationRecord.id.desc())
        .limit(1)
    )


async def revoke_verification(
    db: AsyncSession,
    *,
    verification_id: UUID,
    actor_user_id: UUID,
    reason_code: str,
    now: datetime | None = None,
) -> AgeVerificationRecord:
    if not reason_code.strip():
        raise AgeVerificationError("Revocation reason is required", code="REASON_REQUIRED")
    record = await db.scalar(
        select(AgeVerificationRecord)
        .where(AgeVerificationRecord.id == verification_id)
        .with_for_update()
    )
    if record is None:
        raise AgeVerificationError("Verification was not found", code="NOT_FOUND")
    if record.user_id is not None:
        await lock_compliance_subject(db, record.user_id)
    current = _now(now)
    if record.status is not AgeVerificationStatus.revoked:
        record.status = AgeVerificationStatus.revoked
        record.revoked_at = record.revoked_at or current
        # A human/system revocation terminalizes any browser callback that was
        # issued for this record. Delayed provider callbacks must never restore it.
        record.state_consumed_at = record.state_consumed_at or current
        record.failure_reason_code = reason_code.strip()
        await record_event(
            db,
            "compliance.age_verification_revoked",
            actor_user_id=actor_user_id,
            target_type="age_verification_record",
            target_id=str(record.id),
            metadata={"reason_code": reason_code.strip()},
        )
        if record.user_id:
            await emit_transactional(
                db,
                recipient_user_id=record.user_id,
                notification_type="AGE_VERIFICATION_REVOKED",
                source_domain="compliance",
                source_id=str(record.id),
                title="Age verification changed",
                body="Your age verification is no longer valid. Review the next steps in your account.",
                target_path="/account",
                email=True,
            )
            # LiveKit keeps connected clients alive beyond JWT expiry. Apply
            # the new authority immediately; provider failures are audited by
            # the streaming boundary and retried by scheduled reconciliation.
            from app.streaming.service import evict_user_from_active_live

            await db.flush()
            await evict_user_from_active_live(
                db,
                record.user_id,
                reason="age_verification_revoked",
            )
    else:
        repaired_fields: list[str] = []
        if record.revoked_at is None:
            record.revoked_at = current
            repaired_fields.append("revoked_at")
        if record.state_consumed_at is None:
            record.state_consumed_at = current
            repaired_fields.append("state_consumed_at")
        if record.failure_reason_code is None:
            record.failure_reason_code = reason_code.strip()
            repaired_fields.append("failure_reason_code")
        if repaired_fields:
            await record_event(
                db,
                "compliance.age_verification_revoked",
                actor_user_id=actor_user_id,
                target_type="age_verification_record",
                target_id=str(record.id),
                metadata={
                    "reason_code": reason_code.strip(),
                    "already_revoked": True,
                    "repaired_fields": repaired_fields,
                },
            )
    return record


async def expire_due_verifications(
    db: AsyncSession, *, now: datetime | None = None, limit: int = 500
) -> int:
    current = _now(now)
    processed = 0
    for _ in range(max(1, limit)):
        record = await db.scalar(
            select(AgeVerificationRecord)
            .where(
                AgeVerificationRecord.status == AgeVerificationStatus.verified,
                AgeVerificationRecord.expires_at.is_not(None),
                AgeVerificationRecord.expires_at <= current,
            )
            .order_by(AgeVerificationRecord.expires_at, AgeVerificationRecord.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if record is None:
            break
        if record.user_id is not None:
            # Serialize the expiry authority change with every live token/join
            # mutation for this subject. Once this lock is acquired, a token
            # transaction that read the old record has either committed and is
            # visible to eviction, or has completed before this mutation.
            await lock_compliance_subject(db, record.user_id)
        before = record.status
        record.status = AgeVerificationStatus.expired
        record.failure_reason_code = "VERIFICATION_EXPIRED"
        await record_event(
            db,
            "compliance.age_verification_expired",
            actor_user_id=record.user_id,
            target_type="age_verification_record",
            target_id=str(record.id),
            metadata={
                "before": before.value,
                "after": AgeVerificationStatus.expired.value,
                "effective_at": current.isoformat(),
            },
        )
        await db.flush()
        if record.user_id is not None:
            user = await db.get(User, record.user_id)
            decision = (
                await resolve_compliance_decision(
                    db,
                    user=user,
                    feature=ComplianceFeature.adult_media,
                    adult_restricted=True,
                    now=current,
                )
                if user is not None
                else None
            )
            if (
                decision is not None
                and not decision.age_access_allowed
                and decision.action == "VERIFY_AGE"
            ):
                await emit_transactional(
                    db,
                    recipient_user_id=record.user_id,
                    notification_type="AGE_VERIFICATION_EXPIRED",
                    source_domain="compliance",
                    source_id=f"{record.user_id}:{current.date().isoformat()}",
                    title="Age verification expired",
                    body="Reverify your age to continue using restricted features.",
                    target_path="/account",
                    email=True,
                )
            if decision is not None and not decision.age_access_allowed:
                from app.streaming.service import evict_user_from_active_live

                await evict_user_from_active_live(
                    db,
                    record.user_id,
                    reason="age_verification_expired",
                )
        processed += 1
        # Release the verification/subject/live locks before the next record.
        # A provider outage cannot hold the whole expiry cohort or block
        # callbacks/admin review for the duration of serial timeouts.
        await db.commit()
    return processed


async def notify_expiring_verifications(
    db: AsyncSession,
    *,
    now: datetime | None = None,
    within_days: int = 7,
    limit: int = 500,
) -> int:
    if not 1 <= within_days <= 90:
        raise ValueError("Expiry notification window is invalid")
    current = _now(now)
    deadline = current + timedelta(days=within_days)
    user_ids = (
        await db.scalars(
            select(AgeVerificationRecord.user_id)
            .where(
                AgeVerificationRecord.status == AgeVerificationStatus.verified,
                AgeVerificationRecord.revoked_at.is_(None),
                AgeVerificationRecord.user_id.is_not(None),
                AgeVerificationRecord.expires_at > current,
            )
            .distinct()
        )
    ).all()
    notified = 0
    for user_id in user_ids:
        assert user_id is not None
        user = await db.get(User, user_id)
        if user is None:
            continue
        decision = await resolve_compliance_decision(
            db,
            user=user,
            feature=ComplianceFeature.adult_media,
            adult_restricted=True,
            now=current,
        )
        expiry = decision.verification_expires_at
        if (
            not decision.age_access_allowed
            or expiry is None
            or expiry <= current
            or expiry > deadline
        ):
            continue
        await emit_transactional(
            db,
            recipient_user_id=user_id,
            notification_type="AGE_VERIFICATION_EXPIRING",
            source_domain="compliance",
            source_id=f"{user_id}:{expiry.isoformat()}",
            title="Age verification expiring",
            body="Your age verification will expire soon. Reverify to avoid losing restricted access.",
            target_path="/account",
            email=True,
        )
        notified += 1
        if notified >= limit:
            break
    return notified


async def review_verification(
    db: AsyncSession,
    *,
    verification_id: UUID,
    actor_user_id: UUID,
    status: AgeVerificationStatus,
    change_reason: str,
    achieved_assurance_level: AgeAssuranceLevel | None = None,
    achieved_minimum_age: int | None = None,
    expires_at: datetime | None = None,
    now: datetime | None = None,
) -> AgeVerificationRecord:
    if status not in {
        AgeVerificationStatus.verified,
        AgeVerificationStatus.failed,
        AgeVerificationStatus.revoked,
        AgeVerificationStatus.review_required,
    }:
        raise AgeVerificationError("Review status is invalid", code="INVALID_REVIEW_STATUS")
    if not change_reason.strip():
        raise AgeVerificationError("Review reason is required", code="REASON_REQUIRED")
    current = _now(now)
    record = await db.scalar(
        select(AgeVerificationRecord)
        .where(AgeVerificationRecord.id == verification_id)
        .with_for_update()
    )
    if record is None:
        raise AgeVerificationError("Verification was not found", code="NOT_FOUND")
    if record.user_id is not None:
        await lock_compliance_subject(db, record.user_id)
    before = record.status
    if (
        record.status is AgeVerificationStatus.revoked or record.revoked_at is not None
    ) and status is not AgeVerificationStatus.revoked:
        raise AgeVerificationError(
            "A revoked verification cannot be re-approved or reclassified in place",
            code="REVOCATION_IMMUTABLE",
        )
    if status is AgeVerificationStatus.verified:
        if (
            achieved_assurance_level in {None, AgeAssuranceLevel.none}
            or achieved_minimum_age is None
            or achieved_minimum_age < 1
        ):
            raise AgeVerificationError(
                "Approved review requires normalized assurance and age threshold",
                code="REVIEW_RESULT_INCOMPLETE",
            )
        policy = await effective_policy_for_country(db, record.country_code, now=current)
        if policy is None:
            raise AgeVerificationError(
                "A reviewed current policy is required for approval",
                code="POLICY_UNAVAILABLE",
            )
        if achieved_assurance_level is not policy.rules.required_assurance_level:
            raise AgeVerificationError(
                "Manual assurance must match the current policy requirement",
                code="REVIEW_ASSURANCE_OUT_OF_BOUNDS",
            )
        if achieved_minimum_age != policy.rules.minimum_age:
            raise AgeVerificationError(
                "Manual age threshold must match the current policy requirement",
                code="REVIEW_THRESHOLD_OUT_OF_BOUNDS",
            )
        if expires_at is None:
            raise AgeVerificationError(
                "Manual approval requires a finite expiry",
                code="REVIEW_EXPIRY_REQUIRED",
            )
        expiry = _now(expires_at)
        maximum_expiry = current + timedelta(days=get_settings().manual_age_review_max_days)
        if policy.rules.reverify_after_days is not None:
            maximum_expiry = min(
                maximum_expiry,
                current + timedelta(days=policy.rules.reverify_after_days),
            )
        if expiry <= current or expiry > maximum_expiry:
            raise AgeVerificationError(
                "Manual approval expiry exceeds the configured policy bound",
                code="REVIEW_EXPIRY_OUT_OF_BOUNDS",
            )
        record.achieved_assurance_level = achieved_assurance_level
        record.achieved_minimum_age = achieved_minimum_age
        record.verified_at = current
        record.expires_at = expiry
        record.failed_at = None
        record.revoked_at = None
        record.failure_reason_code = None
    elif status is AgeVerificationStatus.failed:
        record.failed_at = current
        record.failure_reason_code = "ADMIN_REVIEW_FAILED"
    elif status is AgeVerificationStatus.revoked:
        record.revoked_at = record.revoked_at or current
        record.failure_reason_code = record.failure_reason_code or "ADMIN_REVIEW_REVOKED"
    elif status is AgeVerificationStatus.review_required:
        record.failure_reason_code = "ADMIN_REVIEW_REQUIRED"
    # A controlled human decision and a provider callback must never race as
    # competing authorities for the same one-time state. Consuming the state
    # makes every delayed callback reject without changing the reviewed row.
    record.state_consumed_at = record.state_consumed_at or current
    record.status = status
    await record_event(
        db,
        "compliance.age_verification_reviewed",
        actor_user_id=actor_user_id,
        target_type="age_verification_record",
        target_id=str(record.id),
        metadata={
            "before": before.value,
            "after": status.value,
            "change_reason": change_reason.strip(),
            "achieved_assurance": record.achieved_assurance_level.value,
            "achieved_minimum_age": record.achieved_minimum_age,
        },
    )
    if record.user_id:
        if status is AgeVerificationStatus.verified:
            notification_type = "AGE_VERIFICATION_COMPLETED"
            title = "Age verification completed"
            body = "Your age verification is complete."
        elif status is AgeVerificationStatus.revoked:
            notification_type = "AGE_VERIFICATION_REVOKED"
            title = "Age verification changed"
            body = (
                "Your age verification is no longer valid. Review the next steps in your account."
            )
        else:
            notification_type = "AGE_VERIFICATION_ACTION_REQUIRED"
            title = "Age verification needs attention"
            body = "Your age verification needs attention before restricted features are available."
        await emit_transactional(
            db,
            recipient_user_id=record.user_id,
            notification_type=notification_type,
            source_domain="compliance",
            source_id=str(record.id),
            title=title,
            body=body,
            target_path="/account",
            email=True,
        )
        if status is not AgeVerificationStatus.verified:
            from app.streaming.service import evict_user_from_active_live

            await db.flush()
            await evict_user_from_active_live(
                db,
                record.user_id,
                reason=f"age_verification_{status.value}",
            )
    return record


async def probe_provider(
    db: AsyncSession,
    *,
    provider_name: str,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> tuple[ProviderDiagnostic, AgeProviderProbe]:
    settings = settings or get_settings()
    current = _now(now)
    callback_url = (
        f"{settings.api_origin.rstrip('/')}/api/v1/compliance/"
        f"age-verification/callback/{provider_name}"
    )
    try:
        provider = get_age_verification_provider(provider_name, settings=settings)
        diagnostic = await provider.get_provider_status(callback_url)
    except ProviderError as exc:
        raise AgeVerificationError(str(exc), code=exc.code, retryable=exc.retryable) from exc
    if not diagnostic.configuration_complete:
        status = ProviderProbeStatus.misconfigured
    elif diagnostic.healthy and diagnostic.allowed_redirect is not False:
        status = ProviderProbeStatus.healthy
    elif diagnostic.healthy:
        status = ProviderProbeStatus.degraded
    else:
        status = ProviderProbeStatus.unavailable
    row = AgeProviderProbe(
        provider=provider_name,
        environment=provider.environment,
        status=status,
        capabilities_json=diagnostic.capabilities.public_dict(),
        configuration_complete=diagnostic.configuration_complete,
        callback_url=callback_url,
        error_code=diagnostic.error_code,
        probed_at=current,
    )
    db.add(row)
    return diagnostic, row
