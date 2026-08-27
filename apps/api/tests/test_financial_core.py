import asyncio
import hashlib
import hmac
import json

import pytest
from conftest import trusted_self_attested_accounts as accounts
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError

from app.accounts import adult_access
from app.content.access import can_access_content
from app.core.config import Settings, get_settings
from app.creators import service as creators
from app.db.session import SessionLocal
from app.finance import service as finance
from app.models.audit import AuditEvent
from app.models.content import (
    AccessPolicy,
    ContentEntitlement,
    ContentItem,
    ContentStatus,
    ContentType,
    ModerationStatus,
)
from app.models.creator import CreatorStatus
from app.models.finance import (
    ExcessCaptureSource,
    LedgerDirection,
    LedgerEntry,
    LedgerTransaction,
    LedgerTransactionType,
    PaymentAttempt,
    PaymentRefundRequirement,
    PaymentStatus,
    PaymentWebhookEvent,
    Purchase,
    PurchasePaymentAttempt,
    PurchaseStatus,
    RefundRequirementStatus,
)
from app.models.identity import User
from app.models.messaging import UserBlock


async def approved_creator(db, email: str):
    user, _ = await accounts.register(db, email, "strong-password-123", None, adult_confirmed=True)
    profile = await creators.get_or_create_profile(db, user)
    await creators.update_profile(
        db, profile, {"username": email.split("@")[0], "display_name": "Creator"}, user.id
    )
    await creators.submit(db, profile, user.id)
    await creators.development_verify(db, profile, True, user.id)
    await creators.set_status(db, profile, CreatorStatus.approved, user.id)
    await creators.update_profile(db, profile, {"is_public": True}, user.id)
    return user, profile


def failed_payment_payload(attempt: PaymentAttempt) -> tuple[bytes, str]:
    payload = json.dumps(
        {
            "id": f"failed-{attempt.id}",
            "type": "payment.failed",
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(
        get_settings().payment_webhook_secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return payload, signature


@pytest.mark.asyncio
async def test_paid_ppv_is_idempotent_balanced_and_entitles_buyer(db_session):
    owner, profile = await approved_creator(db_session, "finance-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "finance-buyer@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="PPV",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=999,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    buyer.adult_attested_at = None
    buyer.adult_attestation_version = None
    with pytest.raises(finance.FinancialError, match="adult self-attestation"):
        await finance.initiate_purchase(db_session, buyer, content.id, "unattested-request")
    assert await db_session.scalar(select(PaymentAttempt.id)) is None
    adult_access.attest_account(buyer)
    purchase = await finance.initiate_purchase(db_session, buyer, content.id, "same-request")
    assert (
        await finance.initiate_purchase(db_session, buyer, content.id, "same-request")
    ).id == purchase.id
    attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    settled = await finance.process_development_webhook(db_session, payload, signature)
    assert settled and settled.status is PurchaseStatus.paid
    await db_session.flush()
    assert await can_access_content(db_session, content, buyer)
    entries = (
        await db_session.scalars(
            select(LedgerEntry).where(LedgerEntry.transaction_id == settled.ledger_transaction_id)
        )
    ).all()
    debit = sum(entry.amount_minor for entry in entries if entry.direction is LedgerDirection.debit)
    credit = sum(
        entry.amount_minor for entry in entries if entry.direction is LedgerDirection.credit
    )
    assert debit == credit == 999
    assert settled.platform_fee_minor + settled.creator_amount_minor == settled.gross_amount_minor
    assert await finance.process_development_webhook(db_session, payload, signature) is None
    late_failure_payload = json.dumps(
        {
            "id": f"late-failure-{attempt.id}",
            "type": "payment.failed",
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    late_failure_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(),
        late_failure_payload,
        hashlib.sha256,
    ).hexdigest()
    assert (
        await finance.process_development_webhook(
            db_session, late_failure_payload, late_failure_signature
        )
        is None
    )
    assert attempt.status is PaymentStatus.succeeded
    assert settled.status is PurchaseStatus.paid
    ignored_failure = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == "payment.webhook_transition_ignored",
            AuditEvent.target_id == str(attempt.id),
        )
    )
    assert ignored_failure
    assert ignored_failure.metadata_json["reason"] == "failure_requires_pending_attempt"
    assert await db_session.scalar(select(func.count()).select_from(PaymentWebhookEvent)) == 2
    with pytest.raises(DBAPIError):
        await db_session.execute(
            update(LedgerEntry).where(LedgerEntry.id == entries[0].id).values(amount_minor=1)
        )
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_concurrent_same_key_ppv_returns_one_canonical_purchase(db_session, monkeypatch):
    owner, profile = await approved_creator(db_session, "ppv-concurrent-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "ppv-concurrent-buyer@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Concurrent PPV",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=999,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    buyer_id, content_id = buyer.id, content.id
    await db_session.commit()

    first_locked = asyncio.Event()
    release_first = asyncio.Event()
    original_lock = finance.lock_payment_idempotency

    async def paused_lock(db, user_id, key):
        existing = await original_lock(db, user_id, key)
        if existing is None and not first_locked.is_set():
            first_locked.set()
            await asyncio.wait_for(release_first.wait(), timeout=5)
        return existing

    monkeypatch.setattr(finance, "lock_payment_idempotency", paused_lock)

    async def initiate() -> str:
        async with SessionLocal() as session:
            session_buyer = await session.get(User, buyer_id)
            assert session_buyer
            purchase = await finance.initiate_purchase(
                session, session_buyer, content_id, "concurrent-ppv-key"
            )
            await session.commit()
            return str(purchase.id)

    first = asyncio.create_task(initiate())
    await asyncio.wait_for(first_locked.wait(), timeout=5)
    second = asyncio.create_task(initiate())
    await asyncio.sleep(0)
    release_first.set()
    assert len(set(await asyncio.gather(first, second))) == 1
    async with SessionLocal() as verification:
        assert (
            await verification.scalar(
                select(func.count())
                .select_from(PaymentAttempt)
                .where(
                    PaymentAttempt.buyer_user_id == buyer_id,
                    PaymentAttempt.idempotency_key == "concurrent-ppv-key",
                )
            )
            == 1
        )
        assert (
            await verification.scalar(
                select(func.count())
                .select_from(PurchasePaymentAttempt)
                .join(Purchase, Purchase.id == PurchasePaymentAttempt.purchase_id)
                .where(
                    Purchase.buyer_user_id == buyer_id,
                    Purchase.content_id == content_id,
                )
            )
            == 1
        )


@pytest.mark.asyncio
async def test_concurrent_duplicate_provider_event_is_processed_once(db_session, monkeypatch):
    owner, profile = await approved_creator(db_session, "webhook-race-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "webhook-race-buyer@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Webhook race",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=700,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    purchase = await finance.initiate_purchase(db_session, buyer, content.id, "webhook-race")
    attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    attempt_id, purchase_id = attempt.id, purchase.id
    await db_session.commit()

    first_locked = asyncio.Event()
    release_first = asyncio.Event()
    original_lock = finance.lock_payment_webhook_event

    async def paused_lock(db, provider, external_event_id):
        existing = await original_lock(db, provider, external_event_id)
        if existing is None and not first_locked.is_set():
            first_locked.set()
            await asyncio.wait_for(release_first.wait(), timeout=5)
        return existing

    monkeypatch.setattr(finance, "lock_payment_webhook_event", paused_lock)

    async def deliver() -> bool:
        async with SessionLocal() as session:
            result = await finance.process_development_webhook(session, payload, signature)
            await session.commit()
            return result is not None

    first = asyncio.create_task(deliver())
    await asyncio.wait_for(first_locked.wait(), timeout=5)
    second = asyncio.create_task(deliver())
    await asyncio.sleep(0)
    release_first.set()
    assert sorted(await asyncio.gather(first, second)) == [False, True]
    async with SessionLocal() as verification:
        assert (
            await verification.scalar(
                select(func.count())
                .select_from(PaymentWebhookEvent)
                .where(PaymentWebhookEvent.payment_attempt_id == attempt_id)
            )
            == 1
        )
        canonical = await verification.get(Purchase, purchase_id)
        assert canonical and canonical.status is PurchaseStatus.paid
        assert (
            await verification.scalar(
                select(func.count())
                .select_from(LedgerTransaction)
                .where(LedgerTransaction.idempotency_key == f"purchase:{purchase_id}")
            )
            == 1
        )


@pytest.mark.parametrize(
    ("event_type", "purchase_status", "attempt_status", "transaction_type"),
    [
        (
            "payment.refunded",
            PurchaseStatus.refunded,
            PaymentStatus.refunded,
            LedgerTransactionType.refund,
        ),
        (
            "payment.chargeback",
            PurchaseStatus.chargeback,
            PaymentStatus.chargeback,
            LedgerTransactionType.chargeback,
        ),
    ],
)
@pytest.mark.asyncio
async def test_signed_provider_reversal_revokes_ppv_and_exactly_reverses_ledger(
    db_session, event_type, purchase_status, attempt_status, transaction_type
):
    tag = "refund" if attempt_status is PaymentStatus.refunded else "chargeback"
    owner, profile = await approved_creator(db_session, f"reverse-{tag}-owner@example.com")
    buyer, _ = await accounts.register(
        db_session,
        f"reverse-{tag}-buyer@example.com",
        "strong-password-123",
        None,
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.video,
        title="Provider reversal",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=1_250,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    purchase = await finance.initiate_purchase(
        db_session, buyer, content.id, f"provider-reverse-{attempt_status.value}"
    )
    attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    assert attempt
    success_payload, success_signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db_session, success_payload, success_signature)
    original_id = purchase.ledger_transaction_id
    assert original_id and await can_access_content(db_session, content, buyer)
    original_entries = (
        await db_session.scalars(
            select(LedgerEntry).where(LedgerEntry.transaction_id == original_id)
        )
    ).all()

    reversal_payload = json.dumps(
        {
            "id": f"provider-reversal-{attempt_status.value}-{attempt.id}",
            "type": event_type,
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    reversal_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(),
        reversal_payload,
        hashlib.sha256,
    ).hexdigest()
    await finance.process_development_webhook(db_session, reversal_payload, reversal_signature)
    assert purchase.status is purchase_status
    assert attempt.status is attempt_status
    assert not await can_access_content(db_session, content, buyer)
    reversal = await db_session.scalar(
        select(LedgerTransaction).where(
            LedgerTransaction.reversal_of_transaction_id == original_id,
            LedgerTransaction.transaction_type == transaction_type,
        )
    )
    assert reversal
    reversal_entries = (
        await db_session.scalars(
            select(LedgerEntry).where(LedgerEntry.transaction_id == reversal.id)
        )
    ).all()
    assert sorted(
        (entry.ledger_account_id, entry.direction.value, entry.amount_minor)
        for entry in reversal_entries
    ) == sorted(
        (
            entry.ledger_account_id,
            (
                LedgerDirection.credit.value
                if entry.direction is LedgerDirection.debit
                else LedgerDirection.debit.value
            ),
            entry.amount_minor,
        )
        for entry in original_entries
    )
    await finance.process_development_webhook(db_session, reversal_payload, reversal_signature)
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(LedgerTransaction)
            .where(LedgerTransaction.reversal_of_transaction_id == original_id)
        )
        == 1
    )


@pytest.mark.asyncio
async def test_reversal_before_success_is_terminal_traced_and_never_grants_ppv(db_session):
    owner, profile = await approved_creator(db_session, "reverse-first-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "reverse-first-buyer@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Reverse first",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=900,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    purchase = await finance.initiate_purchase(db_session, buyer, content.id, "reverse-first")
    attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    assert attempt
    dispute_payload = json.dumps(
        {
            "id": f"dispute-before-success-{attempt.id}",
            "type": "payment.disputed",
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    dispute_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(), dispute_payload, hashlib.sha256
    ).hexdigest()
    await finance.process_development_webhook(db_session, dispute_payload, dispute_signature)
    assert purchase.status is PurchaseStatus.disputed
    assert attempt.status is PaymentStatus.disputed
    success_while_disputed = json.dumps(
        {
            "id": f"success-while-disputed-{attempt.id}",
            "type": "payment.succeeded",
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    disputed_success_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(),
        success_while_disputed,
        hashlib.sha256,
    ).hexdigest()
    await finance.process_development_webhook(
        db_session, success_while_disputed, disputed_success_signature
    )
    assert attempt.status is PaymentStatus.disputed
    assert purchase.ledger_transaction_id is None and purchase.entitlement_id is None
    reversal_payload = json.dumps(
        {
            "id": f"refund-before-success-{attempt.id}",
            "type": "payment.refunded",
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(
        get_settings().payment_webhook_secret.encode(), reversal_payload, hashlib.sha256
    ).hexdigest()
    await finance.process_development_webhook(db_session, reversal_payload, signature)
    requirement = await db_session.scalar(
        select(PaymentRefundRequirement).where(
            PaymentRefundRequirement.payment_attempt_id == attempt.id
        )
    )
    assert requirement and requirement.status is RefundRequirementStatus.completed
    assert purchase.status is PurchaseStatus.failed
    assert attempt.status is PaymentStatus.refunded
    assert purchase.ledger_transaction_id is None and purchase.entitlement_id is None
    late_success = json.dumps(
        {
            "id": f"success-after-refund-{attempt.id}",
            "type": "payment.succeeded",
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    late_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(), late_success, hashlib.sha256
    ).hexdigest()
    await finance.process_development_webhook(db_session, late_success, late_signature)
    assert attempt.status is PaymentStatus.refunded
    assert purchase.ledger_transaction_id is None and purchase.entitlement_id is None
    assert not await can_access_content(db_session, content, buyer)


@pytest.mark.asyncio
async def test_failed_ppv_can_retry_canonical_purchase_and_settle_once(db_session):
    owner, profile = await approved_creator(db_session, "ppv-retry-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "ppv-retry-buyer@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.video,
        title="Retryable PPV",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=1_299,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()

    purchase = await finance.initiate_purchase(db_session, buyer, content.id, "ppv-first")
    first_attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    assert first_attempt
    failure_payload, failure_signature = failed_payment_payload(first_attempt)
    assert (
        await finance.process_development_webhook(db_session, failure_payload, failure_signature)
        is None
    )
    assert first_attempt.status is PaymentStatus.failed
    assert purchase.status is PurchaseStatus.failed
    replay = await finance.initiate_purchase(db_session, buyer, content.id, "ppv-first")
    assert replay.id == purchase.id and replay.payment_attempt_id == first_attempt.id

    retry = await finance.initiate_purchase(db_session, buyer, content.id, "ppv-second")
    assert retry.id == purchase.id and retry.payment_attempt_id != first_attempt.id
    assert (retry.gross_amount_minor, retry.commission_basis_points) == (1_299, 2_000)
    retry_attempt = await db_session.get(PaymentAttempt, retry.payment_attempt_id)
    assert retry_attempt and retry_attempt.amount_minor == 1_299
    historical_replay = await finance.initiate_purchase(db_session, buyer, content.id, "ppv-first")
    assert historical_replay.id == purchase.id
    assert finance.response_payment_attempt_id(historical_replay) == first_attempt.id
    assert historical_replay.payment_attempt_id == retry_attempt.id
    success_payload, success_signature = finance.development_webhook_payload(retry_attempt)
    assert await finance.process_development_webhook(db_session, success_payload, success_signature)
    assert (
        await finance.process_development_webhook(db_session, success_payload, success_signature)
        is None
    )
    assert purchase.status is PurchaseStatus.paid
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(PurchasePaymentAttempt)
            .where(PurchasePaymentAttempt.purchase_id == purchase.id)
        )
        == 2
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(LedgerTransaction)
            .where(LedgerTransaction.idempotency_key == f"purchase:{purchase.id}")
        )
        == 1
    )
    invalid_attempt = PaymentAttempt(
        buyer_user_id=buyer.id,
        provider="development",
        provider_reference=f"invalid-ordinal-{purchase.id}",
        amount_minor=1_299,
        currency="EUR",
        idempotency_key="invalid-ordinal",
    )
    db_session.add(invalid_attempt)
    await db_session.flush()
    db_session.add(
        PurchasePaymentAttempt(
            purchase_id=purchase.id,
            payment_attempt_id=invalid_attempt.id,
            attempt_number=0,
        )
    )
    with pytest.raises(DBAPIError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_late_success_from_prior_ppv_attempt_settles_and_contains_duplicate(db_session):
    owner, profile = await approved_creator(db_session, "ppv-late-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "ppv-late-buyer@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.video,
        title="Late PPV success",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=1_100,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()

    purchase = await finance.initiate_purchase(db_session, buyer, content.id, "late-first")
    first = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    assert first
    failed_payload, failed_signature = failed_payment_payload(first)
    await finance.process_development_webhook(db_session, failed_payload, failed_signature)
    retry = await finance.initiate_purchase(db_session, buyer, content.id, "late-retry")
    second = await db_session.get(PaymentAttempt, retry.payment_attempt_id)
    assert second and second.id != first.id

    late_payload, late_signature = finance.development_webhook_payload(first)
    settled = await finance.process_development_webhook(db_session, late_payload, late_signature)
    assert settled is purchase and purchase.status is PurchaseStatus.paid
    assert purchase.payment_attempt_id == first.id
    assert await can_access_content(db_session, content, buyer)

    retry_payload, retry_signature = finance.development_webhook_payload(second)
    assert (
        await finance.process_development_webhook(db_session, retry_payload, retry_signature)
        is purchase
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(LedgerTransaction)
            .where(LedgerTransaction.idempotency_key == f"purchase:{purchase.id}")
        )
        == 1
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(ContentEntitlement)
            .where(
                ContentEntitlement.source_type == "purchase",
                ContentEntitlement.source_reference == str(purchase.id),
            )
        )
        == 1
    )
    refund_required = await db_session.scalar(
        select(PaymentRefundRequirement).where(
            PaymentRefundRequirement.payment_attempt_id == second.id
        )
    )
    assert refund_required
    assert (
        refund_required.source_type,
        refund_required.source_reference,
        refund_required.amount_minor,
        refund_required.currency,
        refund_required.status,
        refund_required.reason,
    ) == (
        ExcessCaptureSource.ppv_purchase,
        str(purchase.id),
        1_100,
        "EUR",
        RefundRequirementStatus.required,
        "duplicate_capture",
    )
    liability = await db_session.get(
        LedgerTransaction, refund_required.liability_ledger_transaction_id
    )
    assert liability
    assert liability.transaction_type is LedgerTransactionType.excess_capture_liability
    liability_entries = (
        await db_session.scalars(
            select(LedgerEntry).where(LedgerEntry.transaction_id == liability.id)
        )
    ).all()
    assert sum(
        entry.amount_minor
        for entry in liability_entries
        if entry.direction is LedgerDirection.debit
    ) == sum(
        entry.amount_minor
        for entry in liability_entries
        if entry.direction is LedgerDirection.credit
    )
    duplicate = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == "purchase.duplicate_payment_succeeded",
            AuditEvent.target_id == str(purchase.id),
        )
    )
    assert duplicate and duplicate.metadata_json["duplicate_payment_attempt_id"] == str(second.id)

    refund_payload = json.dumps(
        {
            "id": f"refund-excess-{second.id}",
            "type": "payment.refunded",
            "payment_reference": second.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    refund_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(), refund_payload, hashlib.sha256
    ).hexdigest()
    assert (
        await finance.process_development_webhook(db_session, refund_payload, refund_signature)
        is None
    )
    assert refund_required.status is RefundRequirementStatus.completed
    assert second.status is PaymentStatus.refunded
    refund_ledger = await db_session.get(
        LedgerTransaction, refund_required.refund_ledger_transaction_id
    )
    assert refund_ledger
    assert refund_ledger.transaction_type is LedgerTransactionType.refund
    assert refund_ledger.reversal_of_transaction_id == liability.id
    assert (
        await finance.process_development_webhook(db_session, refund_payload, refund_signature)
        is None
    )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(LedgerTransaction)
            .where(
                LedgerTransaction.reversal_of_transaction_id == liability.id,
                LedgerTransaction.transaction_type == LedgerTransactionType.refund,
            )
        )
        == 1
    )

    ledger_count_after_refund = await db_session.scalar(
        select(func.count()).select_from(LedgerTransaction)
    )
    late_success_payload = json.dumps(
        {
            "id": f"late-success-after-refund-{second.id}",
            "type": "payment.succeeded",
            "payment_reference": second.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    late_success_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(),
        late_success_payload,
        hashlib.sha256,
    ).hexdigest()
    assert (
        await finance.process_development_webhook(
            db_session, late_success_payload, late_success_signature
        )
        is None
    )
    assert second.status is PaymentStatus.refunded
    assert refund_required.status is RefundRequirementStatus.completed
    assert (
        await db_session.scalar(select(func.count()).select_from(LedgerTransaction))
        == ledger_count_after_refund
    )
    ignored_success = await db_session.scalar(
        select(AuditEvent).where(
            AuditEvent.event_type == "payment.webhook_transition_ignored",
            AuditEvent.target_id == str(second.id),
        )
    )
    assert ignored_success
    assert ignored_success.metadata_json["reason"] == "terminal_reversal_dominates"


@pytest.mark.parametrize("creator_blocks_buyer", [False, True])
@pytest.mark.asyncio
async def test_ppv_blocked_relationship_cannot_create_payment_attempt(
    db_session, creator_blocks_buyer
):
    owner, profile = await approved_creator(
        db_session, f"ppv-block-owner-{creator_blocks_buyer}@example.com"
    )
    buyer, _ = await accounts.register(
        db_session,
        f"ppv-block-buyer-{creator_blocks_buyer}@example.com",
        "strong-password-123",
        None,
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Blocked PPV",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=999,
        price_currency="EUR",
    )
    db_session.add_all(
        [
            content,
            UserBlock(
                blocker_user_id=owner.id if creator_blocks_buyer else buyer.id,
                blocked_user_id=buyer.id if creator_blocks_buyer else owner.id,
            ),
        ]
    )
    await db_session.flush()

    with pytest.raises(finance.FinancialError, match="not available"):
        await finance.initiate_purchase(db_session, buyer, content.id, "blocked-ppv")
    assert await db_session.scalar(select(PaymentAttempt.id)) is None


@pytest.mark.asyncio
async def test_full_refund_reverses_value_and_revokes_entitlement(db_session):
    owner, profile = await approved_creator(db_session, "refund-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "refund-buyer@example.com", "strong-password-123", None
    )
    admin, _ = await accounts.register(
        db_session, "refund-admin@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.video,
        title="Refundable PPV",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=1000,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    purchase = await finance.initiate_purchase(db_session, buyer, content.id, "refund-request")
    attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    purchase = await finance.process_development_webhook(db_session, payload, signature)
    assert purchase
    refunded = await finance.refund_purchase(db_session, purchase, admin, "Customer support refund")
    await db_session.flush()
    assert refunded.status is PurchaseStatus.refunded
    assert not await can_access_content(db_session, content, buyer)
    entry_count = await db_session.scalar(select(func.count()).select_from(LedgerEntry))
    assert (
        await finance.refund_purchase(db_session, refunded, admin, "duplicate")
    ).id == refunded.id
    assert await db_session.scalar(select(func.count()).select_from(LedgerEntry)) == entry_count


def test_commission_uses_integer_minor_units_without_rounding_drift():
    assert finance.commission_amount(101, 2000) == (20, 81)
    assert finance.commission_amount(1, 9999) == (0, 1)


def test_payment_webhook_signature_is_required_and_verified():
    payload = b'{"id":"event","type":"payment.succeeded","payment_reference":"ref"}'
    with pytest.raises(finance.FinancialError, match="signature"):
        finance.verify_development_webhook(payload, "invalid")


def test_unimplemented_payment_provider_is_rejected():
    with pytest.raises(RuntimeError, match="PAYMENT_PROVIDER is not implemented"):
        Settings(environment="test", payment_provider="placeholder").validate_production()


@pytest.mark.asyncio
async def test_reconciliation_settles_a_succeeded_attempt_without_duplicate_entries(db_session):
    owner, profile = await approved_creator(db_session, "reconcile-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "reconcile-buyer@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Recoverable PPV",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=500,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    purchase = await finance.initiate_purchase(db_session, buyer, content.id, "recover-settlement")
    attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    assert attempt
    failure_payload, failure_signature = failed_payment_payload(attempt)
    await finance.process_development_webhook(db_session, failure_payload, failure_signature)
    retry = await finance.initiate_purchase(
        db_session, buyer, content.id, "recover-settlement-retry"
    )
    assert retry.payment_attempt_id != attempt.id
    attempt.status = PaymentStatus.succeeded
    assert await finance.reconcile_succeeded_payments(db_session) == 1
    assert purchase.status is PurchaseStatus.paid
    assert purchase.payment_attempt_id == attempt.id
    assert await finance.reconcile_succeeded_payments(db_session) == 0


@pytest.mark.asyncio
async def test_purchase_requires_published_approved_ppv_content(db_session):
    owner, profile = await approved_creator(db_session, "unpublished-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "unpublished-buyer@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Not yet published",
        access_policy=AccessPolicy.ppv,
        price_amount_minor=999,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    with pytest.raises(finance.FinancialError, match="not available"):
        await finance.initiate_purchase(db_session, buyer, content.id, "not-published")


@pytest.mark.asyncio
async def test_commission_snapshot_does_not_change_after_rule_update(db_session):
    owner, profile = await approved_creator(db_session, "commission-owner@example.com")
    buyer_one, _ = await accounts.register(
        db_session, "commission-one@example.com", "strong-password-123", None
    )
    buyer_two, _ = await accounts.register(
        db_session, "commission-two@example.com", "strong-password-123", None
    )
    first = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="First rate",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=999,
        price_currency="EUR",
    )
    second = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Second rate",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=999,
        price_currency="EUR",
    )
    db_session.add_all([first, second])
    await db_session.flush()
    initial = await finance.initiate_purchase(db_session, buyer_one, first.id, "rate-one")
    rule = await db_session.scalar(
        select(finance.CommissionRule).where(finance.CommissionRule.revenue_type == "ppv")
    )
    assert rule
    rule.basis_points = 2500
    later = await finance.initiate_purchase(db_session, buyer_two, second.id, "rate-two")
    assert initial.commission_basis_points == 2000
    assert initial.platform_fee_minor == 199
    assert later.commission_basis_points == 2500
    assert later.platform_fee_minor == 249


@pytest.mark.asyncio
async def test_creator_summary_excludes_refunded_sales_and_keeps_currency_isolated(db_session):
    owner, profile = await approved_creator(db_session, "summary-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "summary-buyer@example.com", "strong-password-123", None
    )
    admin, _ = await accounts.register(
        db_session, "summary-admin@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Summary",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=101,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    purchase = await finance.initiate_purchase(db_session, buyer, content.id, "summary")
    attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    settled = await finance.process_development_webhook(db_session, payload, signature)
    assert settled
    summary = await finance.creator_financial_summary(db_session, profile.id, "EUR")
    assert summary == {
        "pending_amount_minor": 81,
        "available_amount_minor": 0,
        "ppv_gross_amount_minor": 101,
        "platform_fee_amount_minor": 20,
        "creator_net_amount_minor": 81,
        "marketplace_net_amount_minor": 0,
    }
    await finance.refund_purchase(db_session, settled, admin, "support")
    assert (await finance.creator_financial_summary(db_session, profile.id, "EUR"))[
        "ppv_gross_amount_minor"
    ] == 0
    assert await finance.creator_balances(db_session, profile.id, "USD") == {
        "pending_amount_minor": 0,
        "available_amount_minor": 0,
    }


@pytest.mark.asyncio
async def test_earnings_release_is_balanced_and_does_not_reuse_a_previous_balance_key(
    db_session, monkeypatch
):
    owner, profile = await approved_creator(db_session, "release-owner@example.com")
    buyer, _ = await accounts.register(
        db_session, "release-buyer@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Release",
        status=ContentStatus.published,
        moderation_status=ModerationStatus.approved,
        access_policy=AccessPolicy.ppv,
        price_amount_minor=100,
        price_currency="EUR",
    )
    db_session.add(content)
    await db_session.flush()
    purchase = await finance.initiate_purchase(db_session, buyer, content.id, "release")
    attempt = await db_session.get(PaymentAttempt, purchase.payment_attempt_id)
    assert attempt
    payload, signature = finance.development_webhook_payload(attempt)
    assert await finance.process_development_webhook(db_session, payload, signature)
    monkeypatch.setattr(
        finance, "get_settings", lambda: Settings(creator_earnings_settlement_seconds=0)
    )
    release = await finance.release_creator_earnings(db_session, profile.id, "EUR")
    assert release and release.transaction_type.value == "earnings_release"
    assert await finance.creator_balances(db_session, profile.id, "EUR") == {
        "pending_amount_minor": 0,
        "available_amount_minor": 80,
    }
    assert await finance.release_creator_earnings(db_session, profile.id, "EUR") is None
    second_buyer, _ = await accounts.register(
        db_session, "release-buyer-two@example.com", "strong-password-123", None
    )
    second = await finance.initiate_purchase(db_session, second_buyer, content.id, "release-two")
    second_attempt = await db_session.get(PaymentAttempt, second.payment_attempt_id)
    assert second_attempt
    second_payload, second_signature = finance.development_webhook_payload(second_attempt)
    assert await finance.process_development_webhook(db_session, second_payload, second_signature)
    assert await finance.creator_balances(db_session, profile.id, "EUR") == {
        "pending_amount_minor": 80,
        "available_amount_minor": 80,
    }
    dispute_payload = json.dumps(
        {
            "id": f"release-dispute-{attempt.id}",
            "type": "payment.disputed",
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    dispute_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(), dispute_payload, hashlib.sha256
    ).hexdigest()
    await finance.process_development_webhook(db_session, dispute_payload, dispute_signature)
    assert attempt.status is PaymentStatus.disputed
    assert purchase.status is PurchaseStatus.disputed
    # Released A is moved back to its exact held bucket. Pending B remains
    # independently releasable rather than being mistaken for A's hold.
    assert await finance.creator_balances(db_session, profile.id, "EUR") == {
        "pending_amount_minor": 160,
        "available_amount_minor": 0,
    }
    second_release = await finance.release_creator_earnings(db_session, profile.id, "EUR")
    assert second_release and second_release.id != release.id
    assert await finance.creator_balances(db_session, profile.id, "EUR") == {
        "pending_amount_minor": 80,
        "available_amount_minor": 80,
    }
    refund_payload = json.dumps(
        {
            "id": f"release-refund-{attempt.id}",
            "type": "payment.refunded",
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    refund_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(), refund_payload, hashlib.sha256
    ).hexdigest()
    await finance.process_development_webhook(db_session, refund_payload, refund_signature)
    assert await finance.creator_balances(db_session, profile.id, "EUR") == {
        "pending_amount_minor": 0,
        "available_amount_minor": 80,
    }
    chargeback_payload = json.dumps(
        {
            "id": f"release-chargeback-{attempt.id}",
            "type": "payment.chargeback",
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    chargeback_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(), chargeback_payload, hashlib.sha256
    ).hexdigest()
    await finance.process_development_webhook(db_session, chargeback_payload, chargeback_signature)
    assert attempt.status is PaymentStatus.chargeback
    assert purchase.status is PurchaseStatus.chargeback
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(LedgerTransaction)
            .where(LedgerTransaction.reversal_of_transaction_id == purchase.ledger_transaction_id)
        )
        == 1
    )
    late_refund_payload = json.dumps(
        {
            "id": f"release-late-refund-{attempt.id}",
            "type": "payment.refunded",
            "payment_reference": attempt.provider_reference,
        },
        separators=(",", ":"),
    ).encode()
    late_refund_signature = hmac.new(
        get_settings().payment_webhook_secret.encode(),
        late_refund_payload,
        hashlib.sha256,
    ).hexdigest()
    await finance.process_development_webhook(
        db_session, late_refund_payload, late_refund_signature
    )
    assert attempt.status is PaymentStatus.chargeback
    assert purchase.status is PurchaseStatus.chargeback
