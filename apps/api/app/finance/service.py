"""Financial domain services. All value movement is posted as immutable ledger entries."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.audit.service import record_event
from app.compliance.policy import resolve_compliance_decision
from app.compliance.types import (
    ComplianceAccessError,
    ComplianceDecision,
    require_compliance_access,
)
from app.content.access import content_requires_adult_access, public_content_surface_eligible
from app.core.config import get_settings
from app.finance.providers import PaymentProviderError, payment_provider
from app.models.compliance import ComplianceFeature
from app.models.content import (
    AccessPolicy,
    ContentEntitlement,
    ContentItem,
    ContentStatus,
    EntitlementStatus,
    ModerationStatus,
)
from app.models.finance import (
    CommissionRule,
    ExcessCaptureSource,
    LedgerAccount,
    LedgerAccountKind,
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
from app.models.subscription import (
    Subscription,
    SubscriptionPeriod,
    SubscriptionPeriodStatus,
    SubscriptionStatus,
)
from app.notifications.service import emit_transactional


class FinancialError(ValueError):
    def __init__(self, message: str, compliance_decision: ComplianceDecision | None = None):
        super().__init__(message)
        self.compliance_decision = compliance_decision
        self.code = compliance_decision.code if compliance_decision else None
        self.action = compliance_decision.action if compliance_decision else None


async def require_ppv_compliance(
    db: AsyncSession,
    buyer: User,
    content: ContentItem,
    decisions: dict[ComplianceFeature, ComplianceDecision] | None = None,
) -> ComplianceDecision:
    restricted = await content_requires_adult_access(db, content)
    allowed: ComplianceDecision | None = None
    for feature in (ComplianceFeature.ppv, ComplianceFeature.purchases):
        decision = (
            decisions.get(feature)
            if decisions
            else await resolve_compliance_decision(
                db,
                user=buyer,
                feature=feature,
                adult_restricted=restricted,
            )
        )
        if decision is None:
            raise FinancialError("PPV compliance decision is unavailable")
        try:
            allowed = require_compliance_access(decision)
        except ComplianceAccessError as exc:
            raise FinancialError(exc.decision.reason, exc.decision) from exc
    assert allowed is not None
    return allowed


_GENERIC_CREATOR_RELEASE_PROVENANCE = "generic_creator_settlement"


def _generic_creator_release_predicate(
    creator_id: UUID,
    currency: str,
    *,
    effective_at_or_after: datetime | None = None,
):
    """Identify only generic creator-settlement releases.

    Marketplace delivery releases and dispute restorations share the
    ``earnings_release`` transaction type but are order-specific
    reclassifications.  They must never prove that an unrelated PPV or
    subscription allocation left creator-pending, nor consume a generic
    release sequence number.  The legacy key/reference shape keeps existing
    immutable generic releases recognizable after explicit provenance was
    introduced.
    """
    release_prefix = f"release:{creator_id}:{currency}:"
    conditions = [
        LedgerTransaction.transaction_type == LedgerTransactionType.earnings_release,
        LedgerTransaction.currency == currency,
        LedgerTransaction.metadata_json["creator_id"].astext == str(creator_id),
        LedgerTransaction.reversal_of_transaction_id.is_(None),
        LedgerTransaction.metadata_json["marketplace_order_id"].astext.is_(None),
        LedgerTransaction.metadata_json["marketplace_dispute_operation"].astext.is_(None),
        or_(
            LedgerTransaction.metadata_json["release_provenance"].astext
            == _GENERIC_CREATOR_RELEASE_PROVENANCE,
            and_(
                LedgerTransaction.metadata_json["release_provenance"].astext.is_(None),
                LedgerTransaction.idempotency_key.like(f"{release_prefix}%"),
                LedgerTransaction.reference.like(f"{release_prefix}%"),
            ),
        ),
    ]
    if effective_at_or_after is not None:
        conditions.append(LedgerTransaction.effective_at >= effective_at_or_after)
    return and_(*conditions)


def currency_code(value: str) -> str:
    currency = value.upper()
    if len(currency) != 3 or not currency.isalpha():
        raise FinancialError("Currency must be a three-letter ISO code")
    return currency


def commission_amount(gross_amount_minor: int, basis_points: int) -> tuple[int, int]:
    if gross_amount_minor <= 0 or not 0 <= basis_points <= 10_000:
        raise FinancialError("Invalid monetary amount or commission")
    platform_fee = gross_amount_minor * basis_points // 10_000
    return platform_fee, gross_amount_minor - platform_fee


async def lock_payment_idempotency(
    db: AsyncSession, buyer_user_id: UUID, idempotency_key: str
) -> PaymentAttempt | None:
    """Serialize one buyer/key command and return its canonical attempt."""
    lock_scope = f"payment-attempt:{buyer_user_id}:{idempotency_key}"
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_scope, 0))))
    return await db.scalar(
        select(PaymentAttempt).where(
            PaymentAttempt.buyer_user_id == buyer_user_id,
            PaymentAttempt.idempotency_key == idempotency_key,
        )
    )


async def lock_payment_webhook_event(
    db: AsyncSession, provider: str, external_event_id: str
) -> PaymentWebhookEvent | None:
    """Serialize one provider event before its immutable receipt is inserted."""
    lock_scope = f"payment-webhook:{provider}:{external_event_id}"
    await db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_scope, 0))))
    return await db.scalar(
        select(PaymentWebhookEvent).where(
            PaymentWebhookEvent.provider == provider,
            PaymentWebhookEvent.external_event_id == external_event_id,
        )
    )


async def commission_for(db: AsyncSession, revenue_type: str) -> int:
    """Return the server-owned commission rule for one revenue type."""
    if not revenue_type or len(revenue_type) > 64:
        raise FinancialError("Invalid revenue type")
    rule = await db.scalar(
        select(CommissionRule).where(
            CommissionRule.revenue_type == revenue_type, CommissionRule.active.is_(True)
        )
    )
    if rule:
        return rule.basis_points
    rule = CommissionRule(
        revenue_type=revenue_type,
        basis_points=get_settings().finance_default_commission_basis_points,
    )
    db.add(rule)
    await db.flush()
    return rule.basis_points


async def ppv_commission(db: AsyncSession) -> int:
    return await commission_for(db, "ppv")


async def _account(
    db: AsyncSession,
    kind: LedgerAccountKind,
    currency: str,
    owner_creator_id: UUID | None = None,
    owner_group_id: UUID | None = None,
    owner_user_id: UUID | None = None,
    owner_affiliate_partner_id: UUID | None = None,
) -> LedgerAccount:
    query = select(LedgerAccount).where(
        LedgerAccount.kind == kind, LedgerAccount.currency == currency
    )
    if owner_creator_id is None:
        query = query.where(LedgerAccount.owner_creator_id.is_(None))
    else:
        query = query.where(LedgerAccount.owner_creator_id == owner_creator_id)
    if owner_group_id is None:
        query = query.where(LedgerAccount.owner_group_id.is_(None))
    else:
        query = query.where(LedgerAccount.owner_group_id == owner_group_id)
    if owner_user_id is None:
        query = query.where(LedgerAccount.owner_user_id.is_(None))
    else:
        query = query.where(LedgerAccount.owner_user_id == owner_user_id)
    if owner_affiliate_partner_id is None:
        query = query.where(LedgerAccount.owner_affiliate_partner_id.is_(None))
    else:
        query = query.where(LedgerAccount.owner_affiliate_partner_id == owner_affiliate_partner_id)
    account = await db.scalar(query.with_for_update())
    if account:
        return account
    account = LedgerAccount(
        kind=kind,
        currency=currency,
        owner_creator_id=owner_creator_id,
        owner_group_id=owner_group_id,
        owner_user_id=owner_user_id,
        owner_affiliate_partner_id=owner_affiliate_partner_id,
    )
    db.add(account)
    await db.flush()
    return account


async def creator_revenue_allocation(
    db: AsyncSession,
    creator_id: UUID,
    currency: str,
    creator_pool_minor: int,
    event_at: datetime | None = None,
) -> tuple[list[tuple[LedgerAccount, LedgerDirection, int]], dict[str, str]]:
    """Resolve a single active contract and snapshot it in the ledger event.

    This function is the only Phase 8 settlement boundary used by PPV,
    subscriptions, messaging and private live.  A later contract amendment or
    group exit cannot rewrite the returned ledger entries.
    """
    from app.groups.service import active_contract

    contract = await active_contract(db, creator_id, event_at)
    creator_amount = creator_pool_minor
    metadata: dict[str, str] = {
        "creator_id": str(creator_id),
        "creator_pool_minor": str(creator_pool_minor),
    }
    if not contract:
        account = await _account(db, LedgerAccountKind.creator_pending, currency, creator_id)
        metadata.update({"creator_amount_minor": str(creator_amount), "group_amount_minor": "0"})
        return [(account, LedgerDirection.credit, creator_amount)], metadata
    from app.models.groups import GroupCreatorMembership

    membership = await db.get(GroupCreatorMembership, contract.membership_id)
    assert membership
    group_amount = creator_pool_minor * contract.group_basis_points // 10_000
    creator_amount = creator_pool_minor - group_amount
    entries: list[tuple[LedgerAccount, LedgerDirection, int]] = []
    if creator_amount:
        entries.append(
            (
                await _account(db, LedgerAccountKind.creator_pending, currency, creator_id),
                LedgerDirection.credit,
                creator_amount,
            )
        )
    if group_amount:
        entries.append(
            (
                await _account(
                    db,
                    LedgerAccountKind.group_pending,
                    currency,
                    owner_group_id=membership.group_id,
                ),
                LedgerDirection.credit,
                group_amount,
            )
        )
    metadata.update(
        {
            "group_id": str(membership.group_id),
            "group_contract_id": str(contract.id),
            "group_contract_version": str(contract.version),
            "creator_basis_points": str(contract.creator_basis_points),
            "group_basis_points": str(contract.group_basis_points),
            "creator_amount_minor": str(creator_amount),
            "group_amount_minor": str(group_amount),
        }
    )
    return entries, metadata


async def post_entries(
    db: AsyncSession,
    *,
    transaction_type: LedgerTransactionType,
    currency: str,
    idempotency_key: str,
    reference: str,
    entries: list[tuple[LedgerAccount, LedgerDirection, int]],
    reversal_of_transaction_id: UUID | None = None,
    metadata: dict[str, str] | None = None,
) -> LedgerTransaction:
    existing = await db.scalar(
        select(LedgerTransaction).where(LedgerTransaction.idempotency_key == idempotency_key)
    )
    if existing:
        return existing
    debit = sum(amount for _, direction, amount in entries if direction is LedgerDirection.debit)
    credit = sum(amount for _, direction, amount in entries if direction is LedgerDirection.credit)
    if not entries or debit != credit or debit <= 0:
        raise FinancialError("Ledger transaction must balance")
    if any(account.currency != currency or amount <= 0 for account, _, amount in entries):
        raise FinancialError("Ledger entry currency or amount is invalid")
    transaction = LedgerTransaction(
        transaction_type=transaction_type,
        currency=currency,
        idempotency_key=idempotency_key,
        reference=reference,
        reversal_of_transaction_id=reversal_of_transaction_id,
        effective_at=datetime.now(UTC),
        metadata_json=metadata or {},
    )
    db.add(transaction)
    await db.flush()
    db.add_all(
        LedgerEntry(
            transaction_id=transaction.id,
            ledger_account_id=account.id,
            direction=direction,
            amount_minor=amount,
            currency=currency,
        )
        for account, direction, amount in entries
    )
    await db.flush()
    return transaction


async def _lock_original_creator_release_accounts(
    db: AsyncSession,
    original_transaction_id: UUID,
    *,
    payment_attempt_id: UUID,
    provider_event_id: str,
) -> LedgerTransaction | None:
    """Freeze every already released allocation while a dispute is open."""
    original = await db.scalar(
        select(LedgerTransaction)
        .where(LedgerTransaction.id == original_transaction_id)
        .with_for_update()
    )
    if original is None:
        raise FinancialError("Disputed original ledger transaction is missing")
    existing = await db.scalar(
        select(LedgerTransaction).where(
            LedgerTransaction.idempotency_key == f"payment-dispute-hold:{payment_attempt_id}"
        )
    )
    if existing:
        return existing
    from app.referrals.service import dispute_hold_entries

    entries = await dispute_hold_entries(db, original_transaction_id)
    creator_entries = (
        await db.execute(
            select(LedgerEntry, LedgerAccount)
            .join(LedgerAccount, LedgerAccount.id == LedgerEntry.ledger_account_id)
            .where(
                LedgerEntry.transaction_id == original_transaction_id,
                LedgerEntry.direction == LedgerDirection.credit,
                LedgerAccount.kind == LedgerAccountKind.creator_pending,
            )
            .order_by(LedgerAccount.id)
        )
    ).all()
    for original_entry, account in creator_entries:
        pending = await db.scalar(
            select(LedgerAccount).where(LedgerAccount.id == account.id).with_for_update()
        )
        assert pending is not None
        available = await _account(
            db,
            LedgerAccountKind.creator_available,
            account.currency,
            owner_creator_id=account.owner_creator_id,
            owner_group_id=account.owner_group_id,
            owner_user_id=account.owner_user_id,
            owner_affiliate_partner_id=account.owner_affiliate_partner_id,
        )
        released = bool(
            await db.scalar(
                select(
                    exists().where(
                        _generic_creator_release_predicate(
                            account.owner_creator_id,
                            original.currency,
                            effective_at_or_after=original.effective_at,
                        )
                    )
                )
            )
        )
        if released:
            entries.extend(
                [
                    (available, LedgerDirection.debit, original_entry.amount_minor),
                    (pending, LedgerDirection.credit, original_entry.amount_minor),
                ]
            )
    if not entries:
        return None
    return await post_entries(
        db,
        transaction_type=LedgerTransactionType.payment_dispute_hold,
        currency=original.currency,
        idempotency_key=f"payment-dispute-hold:{payment_attempt_id}",
        reference=f"payment_dispute_hold:{payment_attempt_id}",
        entries=entries,
        metadata={
            "payment_attempt_id": str(payment_attempt_id),
            "provider_event_id": provider_event_id,
            "original_ledger_transaction_id": str(original_transaction_id),
        },
    )


async def reverse_original_ledger(
    db: AsyncSession,
    original_transaction_id: UUID,
    *,
    transaction_type: LedgerTransactionType,
    idempotency_key: str,
    reference: str,
    metadata: dict[str, str],
) -> LedgerTransaction:
    """Post one exact frozen-allocation reversal across every command path."""
    original = await db.scalar(
        select(LedgerTransaction)
        .where(LedgerTransaction.id == original_transaction_id)
        .with_for_update()
    )
    if original is None:
        raise FinancialError("Original ledger transaction is missing")
    existing_reversals = (
        await db.scalars(
            select(LedgerTransaction)
            .where(
                LedgerTransaction.reversal_of_transaction_id == original.id,
                LedgerTransaction.transaction_type.in_(
                    [LedgerTransactionType.refund, LedgerTransactionType.chargeback]
                ),
            )
            .order_by(LedgerTransaction.created_at, LedgerTransaction.id)
            .with_for_update()
        )
    ).all()
    original_entries = (
        await db.scalars(select(LedgerEntry).where(LedgerEntry.transaction_id == original.id))
    ).all()
    if not original_entries:
        raise FinancialError("Original ledger allocation is missing")
    existing_entries = (
        (
            await db.scalars(
                select(LedgerEntry).where(
                    LedgerEntry.transaction_id.in_([row.id for row in existing_reversals])
                )
            )
        ).all()
        if existing_reversals
        else []
    )
    original_amount = sum(
        entry.amount_minor for entry in original_entries if entry.direction is LedgerDirection.debit
    )
    reversed_debit = sum(
        entry.amount_minor for entry in existing_entries if entry.direction is LedgerDirection.debit
    )
    reversed_credit = sum(
        entry.amount_minor
        for entry in existing_entries
        if entry.direction is LedgerDirection.credit
    )
    if reversed_debit == reversed_credit == original_amount:
        return existing_reversals[0]
    if reversed_debit > original_amount or reversed_credit > original_amount:
        raise FinancialError("Original ledger allocation is over-reversed")
    from app.referrals.service import reversal_entries as referral_reversal_entries

    referral_entries, referral_allocation = await referral_reversal_entries(db, original.id)
    entries = []
    for entry in original_entries:
        account = await db.get(LedgerAccount, entry.ledger_account_id)
        if account is None:
            raise FinancialError("Original ledger account is missing")
        if (
            referral_allocation
            and entry.direction is LedgerDirection.credit
            and account.kind
            in {LedgerAccountKind.referrer_pending, LedgerAccountKind.affiliate_pending}
        ):
            continue
        if entry.direction is LedgerDirection.credit and account.kind in {
            LedgerAccountKind.creator_pending,
            LedgerAccountKind.group_pending,
        }:
            # Release and reversal lock the same mapped account pair in the
            # same order before reading its mutable balance projection.
            pending = await db.scalar(
                select(LedgerAccount).where(LedgerAccount.id == account.id).with_for_update()
            )
            assert pending is not None
            available = await _account(
                db,
                (
                    LedgerAccountKind.creator_available
                    if account.kind is LedgerAccountKind.creator_pending
                    else LedgerAccountKind.group_available
                ),
                account.currency,
                owner_creator_id=account.owner_creator_id,
                owner_group_id=account.owner_group_id,
                owner_user_id=account.owner_user_id,
                owner_affiliate_partner_id=account.owner_affiliate_partner_id,
            )
            # Generic creator releases drain every then-eligible pending entry
            # atomically and retain creator/currency provenance.  Therefore an
            # allocation belongs in available exactly when such a release was
            # posted after its original transaction.  This avoids consuming a
            # newer pending sale when an older released sale is reversed.
            creator_allocation_released = False
            if account.kind is LedgerAccountKind.creator_pending:
                released = bool(
                    await db.scalar(
                        select(
                            exists().where(
                                _generic_creator_release_predicate(
                                    account.owner_creator_id,
                                    original.currency,
                                    effective_at_or_after=original.effective_at,
                                )
                            )
                        )
                    )
                )
                dispute_held = bool(
                    await db.scalar(
                        select(LedgerTransaction.id).where(
                            LedgerTransaction.transaction_type
                            == LedgerTransactionType.payment_dispute_hold,
                            LedgerTransaction.metadata_json["original_ledger_transaction_id"].astext
                            == str(original.id),
                        )
                    )
                )
                creator_allocation_released = released and not dispute_held
            from_pending = 0 if creator_allocation_released else entry.amount_minor
            if from_pending:
                entries.append((pending, LedgerDirection.debit, from_pending))
            from_available = entry.amount_minor - from_pending
            if from_available:
                entries.append((available, LedgerDirection.debit, from_available))
            continue
        entries.append(
            (
                account,
                LedgerDirection.credit
                if entry.direction is LedgerDirection.debit
                else LedgerDirection.debit,
                entry.amount_minor,
            )
        )
    entries.extend(referral_entries)
    if existing_entries:

        def allocation_key(account: LedgerAccount) -> tuple[str, str, str]:
            paired_kinds = {
                LedgerAccountKind.creator_pending: "creator",
                LedgerAccountKind.creator_available: "creator",
                LedgerAccountKind.group_pending: "group",
                LedgerAccountKind.group_available: "group",
                LedgerAccountKind.referrer_pending: "referrer",
                LedgerAccountKind.referrer_available: "referrer",
                LedgerAccountKind.affiliate_pending: "affiliate",
                LedgerAccountKind.affiliate_available: "affiliate",
            }
            bucket = paired_kinds.get(account.kind)
            if bucket is None:
                return "account", str(account.id), account.currency
            owner = (
                account.owner_creator_id
                or account.owner_group_id
                or account.owner_user_id
                or account.owner_affiliate_partner_id
            )
            return bucket, str(owner), account.currency

        target_by_allocation: dict[tuple[str, str, str], int] = {}
        account_by_allocation: dict[tuple[str, str, str], LedgerAccount] = {}
        for account, direction, amount in entries:
            key = allocation_key(account)
            account_by_allocation[key] = account
            target_by_allocation[key] = target_by_allocation.get(key, 0) + (
                amount if direction is LedgerDirection.credit else -amount
            )
        for entry in existing_entries:
            existing_account = await db.get(LedgerAccount, entry.ledger_account_id)
            if existing_account is None:
                raise FinancialError("Partial reversal ledger account is missing")
            key = allocation_key(existing_account)
            if key not in target_by_allocation:
                raise FinancialError("Partial reversal account does not match frozen allocation")
            target_by_allocation[key] -= (
                entry.amount_minor
                if entry.direction is LedgerDirection.credit
                else -entry.amount_minor
            )
        entries = [
            (
                account_by_allocation[key],
                LedgerDirection.credit if amount > 0 else LedgerDirection.debit,
                abs(amount),
            )
            for key, amount in target_by_allocation.items()
            if amount
        ]
    original_metadata = original.metadata_json or {}
    # Allocation terms are historical financial facts. A reversal must remain
    # explainable after a creator changes contract or leaves a group, so carry
    # the exact original snapshot rather than resolving current configuration.
    frozen_allocation_keys = {
        "creator_id",
        "creator_pool_minor",
        "creator_amount_minor",
        "group_id",
        "group_amount_minor",
        "group_contract_id",
        "group_contract_version",
        "creator_basis_points",
        "group_basis_points",
        "platform_fee_minor",
        "referral_amount_minor",
    }
    frozen_allocation_metadata = {
        key: value for key, value in original_metadata.items() if key in frozen_allocation_keys
    }
    if group_contract_id := original_metadata.get("group_contract_id"):
        frozen_allocation_metadata["original_group_contract_id"] = group_contract_id
    reversal = await post_entries(
        db,
        transaction_type=transaction_type,
        currency=original.currency,
        idempotency_key=idempotency_key,
        reference=reference,
        reversal_of_transaction_id=original.id,
        entries=entries,
        metadata={
            **metadata,
            **frozen_allocation_metadata,
            "original_ledger_transaction_id": str(original.id),
            "reverses_exact_frozen_allocation": "true",
            "prior_partial_reversal_ids": ",".join(str(row.id) for row in existing_reversals),
        },
    )
    if referral_allocation and referral_allocation.reversed_at is None:
        referral_allocation.reversed_at = datetime.now(UTC)
    return reversal


async def record_excess_capture(
    db: AsyncSession,
    attempt: PaymentAttempt,
    *,
    source_type: ExcessCaptureSource | str,
    source_reference: UUID | str,
) -> PaymentRefundRequirement:
    """Freeze and balance one provider capture that must be refunded.

    Every paid domain calls this only after another attempt already owns the
    canonical settlement. The attempt-unique row and ledger key make repeated
    callbacks harmless while keeping the extra cash and refund liability in
    financial truth.
    """
    if attempt.status is not PaymentStatus.succeeded:
        raise FinancialError("Excess-capture containment requires a succeeded attempt")
    existing = await db.scalar(
        select(PaymentRefundRequirement)
        .where(PaymentRefundRequirement.payment_attempt_id == attempt.id)
        .with_for_update()
    )
    if existing:
        return existing
    try:
        resolved_source = ExcessCaptureSource(source_type)
    except ValueError as exc:
        raise FinancialError("Unsupported excess-capture source") from exc
    reference = str(source_reference)
    if not reference or len(reference) > 64:
        raise FinancialError("Invalid excess-capture source reference")
    clearing = await _account(db, LedgerAccountKind.platform_clearing, attempt.currency)
    refund_liability = await _account(db, LedgerAccountKind.refund_clearing, attempt.currency)
    liability = await post_entries(
        db,
        transaction_type=LedgerTransactionType.excess_capture_liability,
        currency=attempt.currency,
        idempotency_key=f"excess-capture:{attempt.id}",
        reference=f"excess_capture:{attempt.id}",
        entries=[
            (clearing, LedgerDirection.debit, attempt.amount_minor),
            (refund_liability, LedgerDirection.credit, attempt.amount_minor),
        ],
        metadata={
            "payment_attempt_id": str(attempt.id),
            "source_type": resolved_source.value,
            "source_reference": reference,
            "state": "refund_required",
        },
    )
    requirement = PaymentRefundRequirement(
        payment_attempt_id=attempt.id,
        source_type=resolved_source,
        source_reference=reference,
        amount_minor=attempt.amount_minor,
        currency=attempt.currency,
        liability_ledger_transaction_id=liability.id,
    )
    db.add(requirement)
    await db.flush()
    await record_event(
        db,
        "payment.excess_capture_refund_required",
        actor_user_id=attempt.buyer_user_id,
        target_type="payment_refund_requirement",
        target_id=str(requirement.id),
        metadata={
            "payment_attempt_id": str(attempt.id),
            "source_type": resolved_source.value,
            "source_reference": reference,
            "liability_ledger_transaction_id": str(liability.id),
        },
    )
    return requirement


async def complete_excess_capture_refund(
    db: AsyncSession,
    attempt: PaymentAttempt,
    provider_refund_reference: str,
    *,
    resolution_type: LedgerTransactionType = LedgerTransactionType.refund,
) -> PaymentRefundRequirement | None:
    """Resolve a refund liability after a signed provider reversal event."""
    requirement = await db.scalar(
        select(PaymentRefundRequirement)
        .where(PaymentRefundRequirement.payment_attempt_id == attempt.id)
        .with_for_update()
    )
    if requirement is None:
        return None
    if requirement.status is RefundRequirementStatus.completed:
        if (
            resolution_type is LedgerTransactionType.chargeback
            and attempt.status is PaymentStatus.refunded
        ):
            attempt.status = PaymentStatus.chargeback
            await record_event(
                db,
                "payment.excess_capture_resolution_upgraded",
                actor_user_id=attempt.buyer_user_id,
                target_type="payment_refund_requirement",
                target_id=str(requirement.id),
                metadata={
                    "provider_chargeback_reference": provider_refund_reference,
                    "prior_refund_ledger_transaction_id": str(
                        requirement.refund_ledger_transaction_id
                    ),
                },
            )
        return requirement
    if not provider_refund_reference or len(provider_refund_reference) > 255:
        raise FinancialError("A provider refund reference is required")
    if resolution_type not in {
        LedgerTransactionType.refund,
        LedgerTransactionType.chargeback,
    }:
        raise FinancialError("Unsupported excess-capture resolution")
    liability = await db.get(LedgerTransaction, requirement.liability_ledger_transaction_id)
    if liability is None:
        raise FinancialError("Excess-capture liability ledger is missing")
    clearing = await _account(db, LedgerAccountKind.platform_clearing, requirement.currency)
    refund_liability = await _account(db, LedgerAccountKind.refund_clearing, requirement.currency)
    refund = await post_entries(
        db,
        transaction_type=resolution_type,
        currency=requirement.currency,
        idempotency_key=f"excess-capture-refund:{requirement.id}",
        reference=f"excess_capture_refund:{requirement.id}",
        reversal_of_transaction_id=liability.id,
        entries=[
            (refund_liability, LedgerDirection.debit, requirement.amount_minor),
            (clearing, LedgerDirection.credit, requirement.amount_minor),
        ],
        metadata={
            "payment_refund_requirement_id": str(requirement.id),
            "payment_attempt_id": str(attempt.id),
            "provider_refund_reference": provider_refund_reference,
            "resolution_type": resolution_type.value,
        },
    )
    requirement.status = RefundRequirementStatus.completed
    requirement.refund_ledger_transaction_id = refund.id
    requirement.provider_refund_reference = provider_refund_reference
    requirement.resolved_at = datetime.now(UTC)
    attempt.status = (
        PaymentStatus.refunded
        if resolution_type is LedgerTransactionType.refund
        else PaymentStatus.chargeback
    )
    await record_event(
        db,
        "payment.excess_capture_resolved",
        actor_user_id=attempt.buyer_user_id,
        target_type="payment_refund_requirement",
        target_id=str(requirement.id),
        metadata={
            "provider_refund_reference": provider_refund_reference,
            "refund_ledger_transaction_id": str(refund.id),
            "resolution_type": resolution_type.value,
        },
    )
    return requirement


async def initiate_purchase(
    db: AsyncSession,
    buyer: User,
    content_id: UUID,
    idempotency_key: str,
    compliance_decisions: dict[ComplianceFeature, ComplianceDecision] | None = None,
) -> Purchase:
    if not idempotency_key or len(idempotency_key) > 128:
        raise FinancialError("A valid Idempotency-Key is required")
    existing_attempt = await lock_payment_idempotency(db, buyer.id, idempotency_key)
    existing = (
        await db.execute(
            select(Purchase, PaymentAttempt)
            .select_from(PurchasePaymentAttempt)
            .join(Purchase, Purchase.id == PurchasePaymentAttempt.purchase_id)
            .join(PaymentAttempt, PaymentAttempt.id == PurchasePaymentAttempt.payment_attempt_id)
            .where(
                PaymentAttempt.buyer_user_id == buyer.id,
                PaymentAttempt.idempotency_key == idempotency_key,
            )
        )
    ).first()
    if existing:
        purchase, replay_attempt = existing
        purchase._response_payment_attempt_id = replay_attempt.id
        return purchase
    if existing_attempt is not None:
        raise FinancialError("Idempotency-Key is already used by another payment command")
    content = await db.scalar(
        select(ContentItem).where(ContentItem.id == content_id).with_for_update()
    )
    if not content or content.access_policy is not AccessPolicy.ppv:
        raise FinancialError("PPV content not found")
    if (
        content.status is not ContentStatus.published
        or content.moderation_status is not ModerationStatus.approved
    ):
        raise FinancialError("PPV content is not available for purchase")
    if content.created_by_user_id == buyer.id:
        raise FinancialError("Creators cannot purchase their own content")
    if not content.price_amount_minor or not content.price_currency:
        raise FinancialError("PPV content is not priced")
    prior = await db.scalar(
        select(Purchase)
        .where(Purchase.buyer_user_id == buyer.id, Purchase.content_id == content.id)
        .with_for_update()
    )
    if prior and prior.status is not PurchaseStatus.failed:
        prior._response_payment_attempt_id = prior.payment_attempt_id
        return prior
    if not await public_content_surface_eligible(db, content, buyer):
        raise FinancialError("PPV content is not available for purchase")
    await require_ppv_compliance(db, buyer, content, compliance_decisions)
    currency = currency_code(prior.currency if prior else content.price_currency)
    bps = prior.commission_basis_points if prior else await ppv_commission(db)
    gross_amount_minor = prior.gross_amount_minor if prior else content.price_amount_minor
    fee, creator_amount = (
        (prior.platform_fee_minor, prior.creator_amount_minor)
        if prior
        else commission_amount(gross_amount_minor, bps)
    )
    attempt = PaymentAttempt(
        buyer_user_id=buyer.id,
        provider=get_settings().payment_provider,
        provider_reference=f"devpay_{secrets.token_urlsafe(18)}",
        amount_minor=gross_amount_minor,
        currency=currency,
        idempotency_key=idempotency_key,
    )
    db.add(attempt)
    await db.flush()
    if prior:
        purchase = prior
        purchase.payment_attempt_id = attempt.id
        purchase.status = PurchaseStatus.awaiting_payment
        attempt_number = (
            int(
                await db.scalar(
                    select(func.coalesce(func.max(PurchasePaymentAttempt.attempt_number), 0)).where(
                        PurchasePaymentAttempt.purchase_id == purchase.id
                    )
                )
                or 0
            )
            + 1
        )
        await record_event(
            db,
            "purchase.payment_retried",
            actor_user_id=buyer.id,
            target_type="purchase",
            target_id=str(purchase.id),
            metadata={"attempt_number": attempt_number},
        )
    else:
        purchase = Purchase(
            buyer_user_id=buyer.id,
            seller_creator_id=content.owner_creator_id,
            content_id=content.id,
            payment_attempt_id=attempt.id,
            gross_amount_minor=gross_amount_minor,
            platform_fee_minor=fee,
            creator_amount_minor=creator_amount,
            commission_basis_points=bps,
            currency=currency,
        )
        db.add(purchase)
        await db.flush()
        attempt_number = 1
    db.add(
        PurchasePaymentAttempt(
            purchase_id=purchase.id,
            payment_attempt_id=attempt.id,
            attempt_number=attempt_number,
        )
    )
    await db.flush()
    purchase._response_payment_attempt_id = attempt.id
    return purchase


def response_payment_attempt_id(purchase: Purchase) -> UUID:
    """Attempt selected by this command, including a historical-key replay."""
    return getattr(purchase, "_response_payment_attempt_id", purchase.payment_attempt_id)


def development_webhook_payload(attempt: PaymentAttempt) -> tuple[bytes, str]:
    provider = payment_provider()
    if not hasattr(provider, "payment_succeeded_payload"):
        raise FinancialError("Development payment flow is unavailable")
    return provider.payment_succeeded_payload(attempt)


def verify_development_webhook(payload: bytes, signature: str | None) -> dict[str, str]:
    try:
        event = payment_provider().verify_webhook(payload, signature)
    except PaymentProviderError as exc:
        raise FinancialError(str(exc)) from exc
    return {
        "id": event.external_event_id,
        "type": event.event_type,
        "payment_reference": event.payment_reference,
    }


async def _record_ignored_payment_transition(
    db: AsyncSession,
    attempt: PaymentAttempt,
    *,
    event_type: str,
    external_event_id: str,
    reason: str,
) -> None:
    await record_event(
        db,
        "payment.webhook_transition_ignored",
        actor_user_id=attempt.buyer_user_id,
        target_type="payment_attempt",
        target_id=str(attempt.id),
        metadata={
            "event_type": event_type,
            "external_event_id": external_event_id,
            "current_status": attempt.status.value,
            "reason": reason,
        },
    )


async def _contain_reversal_before_success(
    db: AsyncSession,
    attempt: PaymentAttempt,
    *,
    event_type: str,
    external_event_id: str,
) -> PaymentRefundRequirement:
    """Persist a capture-and-reversal trace without granting domain value."""
    purchase = await _purchase_for_attempt(db, attempt.id)
    period = await _subscription_period_for_attempt(db, attempt.id)
    source_type: ExcessCaptureSource | None = None
    source_reference: UUID | None = None
    if purchase:
        source_type, source_reference = ExcessCaptureSource.ppv_purchase, purchase.id
        if purchase.payment_attempt_id == attempt.id and purchase.status in {
            PurchaseStatus.awaiting_payment,
            PurchaseStatus.failed,
            PurchaseStatus.disputed,
        }:
            purchase.status = PurchaseStatus.failed
    elif period:
        source_type, source_reference = ExcessCaptureSource.subscription_period, period.id
        from app.subscriptions.service import fail_payment_attempt

        await fail_payment_attempt(db, attempt)
    else:
        from app.models.marketplace import MarketplaceOrder

        order = await db.scalar(
            select(MarketplaceOrder)
            .where(MarketplaceOrder.payment_attempt_id == attempt.id)
            .with_for_update()
        )
        if order:
            source_type, source_reference = ExcessCaptureSource.marketplace_order, order.id
            from app.marketplace.service import release_order_reservation

            await release_order_reservation(db, order.id, "provider_reversed_before_success")
        else:
            from app.featuring.service import booking_for_payment_attempt
            from app.featuring.service import (
                fail_payment_attempt as fail_feature_payment_attempt,
            )

            booking = await booking_for_payment_attempt(db, attempt.id, for_update=True)
            if booking:
                source_type, source_reference = ExcessCaptureSource.feature_booking, booking.id
                await fail_feature_payment_attempt(db, attempt)
            else:
                from app.models.messaging import MessageUnlockPurchase, PendingMessageSend

                unlock = await db.scalar(
                    select(MessageUnlockPurchase)
                    .where(MessageUnlockPurchase.payment_attempt_id == attempt.id)
                    .with_for_update()
                )
                pending_send = None
                if unlock:
                    source_type = ExcessCaptureSource.message_unlock
                    source_reference = unlock.id
                    unlock.status = "failed"
                else:
                    pending_send = await db.scalar(
                        select(PendingMessageSend)
                        .where(PendingMessageSend.payment_attempt_id == attempt.id)
                        .with_for_update()
                    )
                if pending_send:
                    source_type = ExcessCaptureSource.paid_message_send
                    source_reference = pending_send.id
                    pending_send.status = "failed"
                if source_type is None:
                    from app.models.streaming import PrivateSession, PrivateSessionStatus

                    private_session = await db.scalar(
                        select(PrivateSession)
                        .where(PrivateSession.payment_attempt_id == attempt.id)
                        .with_for_update()
                    )
                    if private_session:
                        source_type = ExcessCaptureSource.private_live_session
                        source_reference = private_session.id
                        private_session.status = PrivateSessionStatus.failed
    if source_type is None or source_reference is None:
        raise FinancialError("Payment command association is unavailable for reversal")
    attempt.status = PaymentStatus.succeeded
    attempt.completed_at = attempt.completed_at or datetime.now(UTC)
    requirement = await record_excess_capture(
        db,
        attempt,
        source_type=source_type,
        source_reference=source_reference,
    )
    await complete_excess_capture_refund(
        db,
        attempt,
        external_event_id,
        resolution_type=(
            LedgerTransactionType.refund
            if event_type == "payment.refunded"
            else LedgerTransactionType.chargeback
        ),
    )
    return requirement


async def _open_provider_dispute(
    db: AsyncSession, attempt: PaymentAttempt, *, external_event_id: str
) -> None:
    """Fail closed on value delivery without posting a financial reversal."""
    source_type = "payment_attempt"
    source_reference = str(attempt.id)
    purchase = await _purchase_for_attempt(db, attempt.id)
    if purchase:
        source_type, source_reference = "purchase", str(purchase.id)
        if purchase.payment_attempt_id == attempt.id:
            if purchase.ledger_transaction_id:
                await _lock_original_creator_release_accounts(
                    db,
                    purchase.ledger_transaction_id,
                    payment_attempt_id=attempt.id,
                    provider_event_id=external_event_id,
                )
            purchase.status = PurchaseStatus.disputed
            if purchase.entitlement_id:
                entitlement = await db.get(ContentEntitlement, purchase.entitlement_id)
                if entitlement:
                    entitlement.status = EntitlementStatus.revoked
                    entitlement.valid_until = datetime.now(UTC)
    else:
        period = await _subscription_period_for_attempt(db, attempt.id)
        if period:
            source_type, source_reference = "subscription_period", str(period.id)
            if period.payment_attempt_id == attempt.id:
                if period.ledger_transaction_id:
                    await _lock_original_creator_release_accounts(
                        db,
                        period.ledger_transaction_id,
                        payment_attempt_id=attempt.id,
                        provider_event_id=external_event_id,
                    )
                period.status = SubscriptionPeriodStatus.disputed
                if period.entitlement_id:
                    entitlement = await db.get(ContentEntitlement, period.entitlement_id)
                    if entitlement:
                        entitlement.status = EntitlementStatus.revoked
                        entitlement.valid_until = datetime.now(UTC)
                subscription = await db.scalar(
                    select(Subscription)
                    .where(Subscription.id == period.subscription_id)
                    .with_for_update()
                )
                if (
                    subscription
                    and subscription.current_period_start == period.period_start
                    and subscription.current_period_end == period.period_end
                ):
                    subscription.status = SubscriptionStatus.suspended
                    subscription.auto_renew = False
        else:
            from app.marketplace import service as marketplace_service
            from app.models.marketplace import MarketplaceOrder, MarketplaceOrderStatus

            order = await db.scalar(
                select(MarketplaceOrder)
                .where(MarketplaceOrder.payment_attempt_id == attempt.id)
                .with_for_update()
            )
            if order:
                source_type, source_reference = "marketplace_order", str(order.id)
                if order.status is MarketplaceOrderStatus.awaiting_payment:
                    await marketplace_service.release_order_reservation(
                        db, order.id, "provider_dispute_before_success"
                    )
                elif order.status in {
                    MarketplaceOrderStatus.paid,
                    MarketplaceOrderStatus.processing,
                    MarketplaceOrderStatus.shipped,
                    MarketplaceOrderStatus.delivered,
                }:
                    await marketplace_service.block_order_for_dispute(
                        db,
                        order,
                        attempt,
                        None,
                        "provider_dispute",
                        provider_event_id=external_event_id,
                    )
            else:
                from app.featuring.service import booking_for_payment_attempt
                from app.models.featuring import FeatureBookingStatus

                booking = await booking_for_payment_attempt(db, attempt.id, for_update=True)
                if booking:
                    source_type, source_reference = "feature_booking", str(booking.id)
                    if booking.payment_attempt_id == attempt.id:
                        booking.status = FeatureBookingStatus.suspended
                        booking.reservation_expires_at = None
                        booking.ended_at = booking.ended_at or datetime.now(UTC)
                else:
                    from app.models.messaging import MessageUnlockPurchase, PendingMessageSend

                    charge = await db.scalar(
                        select(MessageUnlockPurchase)
                        .where(MessageUnlockPurchase.payment_attempt_id == attempt.id)
                        .with_for_update()
                    )
                    if charge is None:
                        charge = await db.scalar(
                            select(PendingMessageSend)
                            .where(PendingMessageSend.payment_attempt_id == attempt.id)
                            .with_for_update()
                        )
                    if charge:
                        source_type, source_reference = type(charge).__tablename__, str(charge.id)
                        if charge.ledger_transaction_id:
                            await _lock_original_creator_release_accounts(
                                db,
                                charge.ledger_transaction_id,
                                payment_attempt_id=attempt.id,
                                provider_event_id=external_event_id,
                            )
                        charge.status = "disputed"
                    else:
                        from app.models.streaming import PrivateSession, PrivateSessionStatus

                        private_session = await db.scalar(
                            select(PrivateSession)
                            .where(PrivateSession.payment_attempt_id == attempt.id)
                            .with_for_update()
                        )
                        if private_session:
                            from app.streaming.service import (
                                block_private_session_for_dispute,
                            )

                            await block_private_session_for_dispute(
                                db,
                                private_session,
                                reason="provider_dispute",
                            )
                            # The committed outbox intent owns provider room
                            # termination. Financial/product authority is
                            # nevertheless terminal in this same transaction;
                            # callers must never observe a merely "ending"
                            # disputed session as purchasable or reconnectable.
                            private_session.status = PrivateSessionStatus.disputed
                            from app.models.streaming import PrivateSessionSettlement

                            settlement = await db.scalar(
                                select(PrivateSessionSettlement).where(
                                    PrivateSessionSettlement.private_session_id
                                    == private_session.id
                                )
                            )
                            if settlement:
                                await _lock_original_creator_release_accounts(
                                    db,
                                    settlement.ledger_transaction_id,
                                    payment_attempt_id=attempt.id,
                                    provider_event_id=external_event_id,
                                )
                            source_type = "private_session"
                            source_reference = str(private_session.id)
    attempt.status = PaymentStatus.disputed
    await record_event(
        db,
        "payment.provider_disputed",
        actor_user_id=attempt.buyer_user_id,
        target_type=source_type,
        target_id=source_reference,
        metadata={
            "payment_attempt_id": str(attempt.id),
            "provider_event_id": external_event_id,
        },
    )


async def process_development_webhook(
    db: AsyncSession, payload: bytes, signature: str | None
) -> Purchase | None:
    event = verify_development_webhook(payload, signature)
    existing = await lock_payment_webhook_event(db, "development", event["id"])
    if existing:
        return None
    attempt = await db.scalar(
        select(PaymentAttempt)
        .where(
            PaymentAttempt.provider == "development",
            PaymentAttempt.provider_reference == event["payment_reference"],
        )
        .with_for_update()
    )
    webhook_event = PaymentWebhookEvent(
        provider="development", external_event_id=event["id"], event_type=event["type"]
    )
    db.add(webhook_event)
    if not attempt:
        await db.flush()
        webhook_event.processed_at = datetime.now(UTC)
        return None
    webhook_event.payment_attempt_id = attempt.id
    if event["type"] == "payment.succeeded" and attempt.status in {
        PaymentStatus.succeeded,
        PaymentStatus.refunded,
        PaymentStatus.disputed,
        PaymentStatus.chargeback,
    }:
        await _record_ignored_payment_transition(
            db,
            attempt,
            event_type=event["type"],
            external_event_id=event["id"],
            reason=(
                "duplicate_success"
                if attempt.status is PaymentStatus.succeeded
                else "terminal_reversal_dominates"
            ),
        )
        webhook_event.processed_at = datetime.now(UTC)
        return None
    if event["type"] == "payment.disputed":
        if attempt.status in {
            PaymentStatus.disputed,
            PaymentStatus.refunded,
            PaymentStatus.chargeback,
        }:
            await _record_ignored_payment_transition(
                db,
                attempt,
                event_type=event["type"],
                external_event_id=event["id"],
                reason="terminal_reversal_dominates",
            )
            webhook_event.processed_at = datetime.now(UTC)
            return None
        await _open_provider_dispute(db, attempt, external_event_id=event["id"])
        webhook_event.processed_at = datetime.now(UTC)
        return None
    if event["type"] in {"payment.refunded", "payment.chargeback"}:
        if attempt.status is PaymentStatus.chargeback or (
            attempt.status is PaymentStatus.refunded and event["type"] == "payment.refunded"
        ):
            await _record_ignored_payment_transition(
                db,
                attempt,
                event_type=event["type"],
                external_event_id=event["id"],
                reason="terminal_reversal_dominates",
            )
            webhook_event.processed_at = datetime.now(UTC)
            return None
        if attempt.status in {
            PaymentStatus.pending,
            PaymentStatus.failed,
        } or (attempt.status is PaymentStatus.disputed and attempt.completed_at is None):
            await _contain_reversal_before_success(
                db,
                attempt,
                event_type=event["type"],
                external_event_id=event["id"],
            )
            webhook_event.processed_at = datetime.now(UTC)
            return None
        completed_requirement = await complete_excess_capture_refund(
            db,
            attempt,
            event["id"],
            resolution_type=(
                LedgerTransactionType.refund
                if event["type"] == "payment.refunded"
                else LedgerTransactionType.chargeback
            ),
        )
        if completed_requirement:
            webhook_event.processed_at = datetime.now(UTC)
            return None
        reversal_type = (
            LedgerTransactionType.refund
            if event["type"] == "payment.refunded"
            else LedgerTransactionType.chargeback
        )
        purchase = await _purchase_for_attempt(db, attempt.id)
        if purchase and await _reverse_provider_purchase(
            db, purchase, attempt, reversal_type, event["id"]
        ):
            webhook_event.processed_at = datetime.now(UTC)
            return None
        period = await _subscription_period_for_attempt(db, attempt.id)
        if period and await _reverse_provider_subscription(
            db, period, attempt, reversal_type, event["id"]
        ):
            webhook_event.processed_at = datetime.now(UTC)
            return None
        from app.featuring.service import booking_for_payment_attempt

        booking = await booking_for_payment_attempt(db, attempt.id, for_update=True)
        if booking and booking.payment_attempt_id == attempt.id and booking.ledger_transaction_id:
            from app.models.featuring import FeatureBookingStatus

            reversal = await reverse_original_ledger(
                db,
                booking.ledger_transaction_id,
                transaction_type=reversal_type,
                idempotency_key=f"provider-reversal:feature-booking:{booking.id}",
                reference=f"provider_reversal:feature_booking:{booking.id}",
                metadata={
                    "feature_booking_id": str(booking.id),
                    "provider_event_id": event["id"],
                },
            )
            booking.status = (
                FeatureBookingStatus.refunded
                if reversal_type is LedgerTransactionType.refund
                else FeatureBookingStatus.chargeback
            )
            attempt.status = (
                PaymentStatus.refunded
                if reversal_type is LedgerTransactionType.refund
                else PaymentStatus.chargeback
            )
            await record_event(
                db,
                f"featuring.provider_{reversal_type.value}",
                actor_user_id=booking.purchaser_user_id,
                target_type="feature_booking",
                target_id=str(booking.id),
                metadata={
                    "provider_event_id": event["id"],
                    "reversal_ledger_transaction_id": str(reversal.id),
                },
            )
            webhook_event.processed_at = datetime.now(UTC)
            return None
        from app.marketplace import service as marketplace_service
        from app.models.marketplace import MarketplaceOrder

        order = await db.scalar(
            select(MarketplaceOrder)
            .where(MarketplaceOrder.payment_attempt_id == attempt.id)
            .with_for_update()
        )
        if order:
            if event["type"] == "payment.refunded":
                await marketplace_service.refund_order(db, order.id, None, "provider_refund")
            elif event["type"] == "payment.chargeback":
                await marketplace_service.chargeback_order(
                    db, order.id, None, "provider_chargeback"
                )
            webhook_event.processed_at = datetime.now(UTC)
            return None
        if await _reverse_provider_messaging(db, attempt, reversal_type, event["id"]):
            webhook_event.processed_at = datetime.now(UTC)
            return None
        from app.streaming.service import (
            reverse_live_commerce_charge,
            reverse_private_session_payment,
        )

        if await reverse_live_commerce_charge(
            db,
            attempt,
            resolution_type=reversal_type,
            provider_event_id=event["id"],
        ):
            attempt.status = (
                PaymentStatus.refunded
                if reversal_type is LedgerTransactionType.refund
                else PaymentStatus.chargeback
            )
            webhook_event.processed_at = datetime.now(UTC)
            return None

        if await reverse_private_session_payment(
            db,
            attempt,
            resolution_type=reversal_type,
            reason=f"provider:{event['id']}",
        ):
            webhook_event.processed_at = datetime.now(UTC)
            return None
        webhook_event.processed_at = datetime.now(UTC)
        return None
    if event["type"] != "payment.succeeded":
        if attempt.status is not PaymentStatus.pending:
            await _record_ignored_payment_transition(
                db,
                attempt,
                event_type=event["type"],
                external_event_id=event["id"],
                reason="failure_requires_pending_attempt",
            )
            webhook_event.processed_at = datetime.now(UTC)
            return None
        attempt.status = PaymentStatus.failed
        purchase = await _purchase_for_attempt(db, attempt.id)
        if (
            purchase
            and purchase.payment_attempt_id == attempt.id
            and purchase.status is PurchaseStatus.awaiting_payment
        ):
            purchase.status = PurchaseStatus.failed
            await record_event(
                db,
                "purchase.payment_failed",
                actor_user_id=purchase.buyer_user_id,
                target_type="purchase",
                target_id=str(purchase.id),
            )
        from app.marketplace.service import release_order_reservation
        from app.models.marketplace import MarketplaceOrder

        order = await db.scalar(
            select(MarketplaceOrder)
            .where(MarketplaceOrder.payment_attempt_id == attempt.id)
            .with_for_update()
        )
        if order:
            await release_order_reservation(db, order.id, "payment_failed")
        from app.subscriptions.service import fail_payment_attempt

        await fail_payment_attempt(db, attempt)
        from app.featuring.service import fail_payment_attempt as fail_feature_payment_attempt

        await fail_feature_payment_attempt(db, attempt)
        from app.streaming.service import fail_live_commerce_charge

        await fail_live_commerce_charge(db, attempt)
        webhook_event.processed_at = datetime.now(UTC)
        return None
    attempt.status, attempt.completed_at = PaymentStatus.succeeded, datetime.now(UTC)
    purchase = await _purchase_for_attempt(db, attempt.id)
    if purchase:
        await _settle_or_contain_purchase_success(db, purchase, attempt)
    elif not purchase:
        from app.featuring.service import booking_for_payment_attempt
        from app.featuring.service import (
            settle_payment_attempt as settle_feature_payment_attempt,
        )

        booking = await booking_for_payment_attempt(db, attempt.id, for_update=True)
        if booking:
            await settle_feature_payment_attempt(db, attempt)
        else:
            from app.messaging.service import settle_message_unlock, settle_paid_send
            from app.models.messaging import MessageUnlockPurchase, PendingMessageSend

            unlock = await db.scalar(
                select(MessageUnlockPurchase)
                .where(MessageUnlockPurchase.payment_attempt_id == attempt.id)
                .with_for_update()
            )
            if unlock:
                await settle_message_unlock(db, unlock)
            else:
                pending_send = await db.scalar(
                    select(PendingMessageSend)
                    .where(PendingMessageSend.payment_attempt_id == attempt.id)
                    .with_for_update()
                )
                if pending_send:
                    await settle_paid_send(db, pending_send)
                else:
                    from app.models.streaming import PrivateSession
                    from app.streaming.service import (
                        authorize_private_session,
                        settle_live_commerce_charge,
                    )

                    session = await db.scalar(
                        select(PrivateSession)
                        .where(PrivateSession.payment_attempt_id == attempt.id)
                        .with_for_update()
                    )
                    if session:
                        await authorize_private_session(db, session)
                    else:
                        if await settle_live_commerce_charge(db, attempt) is None:
                            from app.marketplace.service import (
                                settle_or_contain_payment_attempt as settle_marketplace_attempt,
                            )

                            if await settle_marketplace_attempt(db, attempt) is None:
                                from app.subscriptions.service import settle_payment_attempt

                                await settle_payment_attempt(db, attempt)
    webhook_event.processed_at = datetime.now(UTC)
    return purchase


async def _purchase_for_attempt(db: AsyncSession, attempt_id: UUID) -> Purchase | None:
    """Resolve the canonical purchase through durable attempt history."""
    return await db.scalar(
        select(Purchase)
        .join(
            PurchasePaymentAttempt,
            PurchasePaymentAttempt.purchase_id == Purchase.id,
        )
        .where(PurchasePaymentAttempt.payment_attempt_id == attempt_id)
        .with_for_update(of=Purchase)
        .execution_options(populate_existing=True)
    )


async def _subscription_period_for_attempt(
    db: AsyncSession, attempt_id: UUID
) -> SubscriptionPeriod | None:
    from app.models.subscription import SubscriptionRenewalAttempt

    return await db.scalar(
        select(SubscriptionPeriod)
        .join(
            SubscriptionRenewalAttempt,
            SubscriptionRenewalAttempt.subscription_period_id == SubscriptionPeriod.id,
        )
        .where(SubscriptionRenewalAttempt.payment_attempt_id == attempt_id)
        .with_for_update(of=SubscriptionPeriod)
        .execution_options(populate_existing=True)
    )


async def _reverse_provider_purchase(
    db: AsyncSession,
    purchase: Purchase,
    attempt: PaymentAttempt,
    transaction_type: LedgerTransactionType,
    provider_event_id: str,
) -> bool:
    if (
        purchase.status
        not in {
            PurchaseStatus.paid,
            PurchaseStatus.disputed,
            *(
                [PurchaseStatus.refunded]
                if transaction_type is LedgerTransactionType.chargeback
                else []
            ),
        }
        or purchase.payment_attempt_id != attempt.id
        or purchase.ledger_transaction_id is None
    ):
        return False
    reversal = await reverse_original_ledger(
        db,
        purchase.ledger_transaction_id,
        transaction_type=transaction_type,
        idempotency_key=f"provider-{transaction_type.value}:purchase:{purchase.id}",
        reference=f"provider_{transaction_type.value}:purchase:{purchase.id}",
        metadata={
            "purchase_id": str(purchase.id),
            "provider_event_id": provider_event_id,
        },
    )
    entitlement = await db.get(ContentEntitlement, purchase.entitlement_id)
    if entitlement:
        entitlement.status = EntitlementStatus.revoked
        entitlement.valid_until = datetime.now(UTC)
    purchase.status = (
        PurchaseStatus.refunded
        if transaction_type is LedgerTransactionType.refund
        else PurchaseStatus.chargeback
    )
    attempt.status = (
        PaymentStatus.refunded
        if transaction_type is LedgerTransactionType.refund
        else PaymentStatus.chargeback
    )
    await record_event(
        db,
        f"purchase.provider_{transaction_type.value}",
        actor_user_id=None,
        target_type="purchase",
        target_id=str(purchase.id),
        metadata={
            "provider_event_id": provider_event_id,
            "reversal_ledger_transaction_id": str(reversal.id),
        },
    )
    return True


async def _reverse_provider_subscription(
    db: AsyncSession,
    period: SubscriptionPeriod,
    attempt: PaymentAttempt,
    transaction_type: LedgerTransactionType,
    provider_event_id: str,
) -> bool:
    if (
        period.status
        not in {
            SubscriptionPeriodStatus.active,
            SubscriptionPeriodStatus.disputed,
            *(
                [SubscriptionPeriodStatus.refunded]
                if transaction_type is LedgerTransactionType.chargeback
                else []
            ),
        }
        or period.payment_attempt_id != attempt.id
        or period.ledger_transaction_id is None
    ):
        return False
    reversal = await reverse_original_ledger(
        db,
        period.ledger_transaction_id,
        transaction_type=transaction_type,
        idempotency_key=f"provider-{transaction_type.value}:subscription-period:{period.id}",
        reference=f"provider_{transaction_type.value}:subscription_period:{period.id}",
        metadata={
            "subscription_period_id": str(period.id),
            "provider_event_id": provider_event_id,
        },
    )
    entitlement = await db.get(ContentEntitlement, period.entitlement_id)
    if entitlement:
        entitlement.status = EntitlementStatus.revoked
        entitlement.valid_until = datetime.now(UTC)
    period.status = (
        SubscriptionPeriodStatus.refunded
        if transaction_type is LedgerTransactionType.refund
        else SubscriptionPeriodStatus.chargeback
    )
    subscription = await db.get(Subscription, period.subscription_id)
    if subscription and subscription.current_period_end == period.period_end:
        subscription.status = SubscriptionStatus.expired
        subscription.auto_renew = False
        subscription.cancel_at_period_end = True
        subscription.ended_at = datetime.now(UTC)
    attempt.status = (
        PaymentStatus.refunded
        if transaction_type is LedgerTransactionType.refund
        else PaymentStatus.chargeback
    )
    await record_event(
        db,
        f"subscription.period_provider_{transaction_type.value}",
        actor_user_id=None,
        target_type="subscription_period",
        target_id=str(period.id),
        metadata={
            "provider_event_id": provider_event_id,
            "reversal_ledger_transaction_id": str(reversal.id),
        },
    )
    return True


async def _reverse_provider_messaging(
    db: AsyncSession,
    attempt: PaymentAttempt,
    transaction_type: LedgerTransactionType,
    provider_event_id: str,
) -> bool:
    from app.models.messaging import MessageUnlockPurchase, PendingMessageSend

    charge = await db.scalar(
        select(MessageUnlockPurchase)
        .where(MessageUnlockPurchase.payment_attempt_id == attempt.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    source_type = "message_unlock_purchase"
    if charge is None:
        charge = await db.scalar(
            select(PendingMessageSend)
            .where(PendingMessageSend.payment_attempt_id == attempt.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        source_type = "pending_message_send"
    allowed_statuses = {"paid", "disputed"}
    if transaction_type is LedgerTransactionType.chargeback:
        allowed_statuses.add("refunded")
    if (
        charge is None
        or charge.status not in allowed_statuses
        or charge.ledger_transaction_id is None
    ):
        return False
    reversal = await reverse_original_ledger(
        db,
        charge.ledger_transaction_id,
        transaction_type=transaction_type,
        idempotency_key=(f"provider-{transaction_type.value}:{source_type}:{charge.id}"),
        reference=f"provider_{transaction_type.value}:{source_type}:{charge.id}",
        metadata={source_type + "_id": str(charge.id), "provider_event_id": provider_event_id},
    )
    charge.status = "refunded" if transaction_type is LedgerTransactionType.refund else "chargeback"
    attempt.status = (
        PaymentStatus.refunded
        if transaction_type is LedgerTransactionType.refund
        else PaymentStatus.chargeback
    )
    await record_event(
        db,
        f"messaging.provider_{transaction_type.value}",
        actor_user_id=None,
        target_type=source_type,
        target_id=str(charge.id),
        metadata={
            "provider_event_id": provider_event_id,
            "reversal_ledger_transaction_id": str(reversal.id),
        },
    )
    return True


async def _settle_or_contain_purchase_success(
    db: AsyncSession, purchase: Purchase, attempt: PaymentAttempt
) -> bool:
    """Settle the first provider success and audit any later duplicate charge."""
    if purchase.status in {PurchaseStatus.awaiting_payment, PurchaseStatus.failed}:
        await settle_purchase(db, purchase, attempt)
        return True
    if purchase.status is PurchaseStatus.paid and purchase.payment_attempt_id != attempt.id:
        requirement = await record_excess_capture(
            db,
            attempt,
            source_type=ExcessCaptureSource.ppv_purchase,
            source_reference=purchase.id,
        )
        await record_event(
            db,
            "purchase.duplicate_payment_succeeded",
            actor_user_id=purchase.buyer_user_id,
            target_type="purchase",
            target_id=str(purchase.id),
            metadata={
                "settled_payment_attempt_id": str(purchase.payment_attempt_id),
                "duplicate_payment_attempt_id": str(attempt.id),
                "refund_requirement_id": str(requirement.id),
                "containment": "refund_required_no_duplicate_ledger_or_entitlement",
            },
        )
    return False


async def settle_purchase(
    db: AsyncSession,
    purchase: Purchase,
    payment_attempt: PaymentAttempt | None = None,
) -> Purchase:
    if purchase.status is PurchaseStatus.paid:
        return purchase
    attempt = payment_attempt or await db.get(PaymentAttempt, purchase.payment_attempt_id)
    if not attempt or attempt.status is not PaymentStatus.succeeded:
        raise FinancialError("Purchase settlement requires a succeeded payment attempt")
    # The first verified success wins even when it arrives after a retry rotated
    # the command pointer. Later successes remain visible in attempt history but
    # cannot create a second ledger transaction or entitlement.
    purchase.payment_attempt_id = attempt.id
    clearing = await _account(db, LedgerAccountKind.platform_clearing, purchase.currency)
    revenue = await _account(db, LedgerAccountKind.platform_revenue, purchase.currency)
    allocation_entries, allocation_metadata = await creator_revenue_allocation(
        db,
        purchase.seller_creator_id,
        purchase.currency,
        purchase.creator_amount_minor,
        attempt.completed_at or purchase.created_at,
    )
    from app.referrals.service import record_revenue_allocation, revenue_allocation

    event_at = attempt.completed_at or purchase.created_at
    referral_entries, referral_allocation = await revenue_allocation(
        db,
        buyer_user_id=purchase.buyer_user_id,
        revenue_type="ppv",
        currency=purchase.currency,
        platform_fee_minor=purchase.platform_fee_minor,
        occurred_at=event_at,
    )
    referral_amount = int(referral_allocation["amount_minor"]) if referral_allocation else 0
    ledger = await post_entries(
        db,
        transaction_type=LedgerTransactionType.ppv_purchase,
        currency=purchase.currency,
        idempotency_key=f"purchase:{purchase.id}",
        reference=f"ppv_purchase:{purchase.id}",
        entries=[
            (clearing, LedgerDirection.debit, purchase.gross_amount_minor),
            (revenue, LedgerDirection.credit, purchase.platform_fee_minor - referral_amount),
            *referral_entries,
            *allocation_entries,
        ],
        metadata={
            "purchase_id": str(purchase.id),
            "content_id": str(purchase.content_id),
            "platform_fee_minor": str(purchase.platform_fee_minor),
            "referral_amount_minor": str(referral_amount),
            **allocation_metadata,
        },
    )
    await record_revenue_allocation(
        db,
        source_ledger_transaction_id=ledger.id,
        allocation=referral_allocation,
    )
    entitlement = await db.scalar(
        select(ContentEntitlement).where(
            ContentEntitlement.source_type == "purchase",
            ContentEntitlement.source_reference == str(purchase.id),
        )
    )
    if not entitlement:
        entitlement = ContentEntitlement(
            subject_user_id=purchase.buyer_user_id,
            content_id=purchase.content_id,
            source_type="purchase",
            source_reference=str(purchase.id),
            valid_from=datetime.now(UTC),
        )
        db.add(entitlement)
        await db.flush()
    purchase.status = PurchaseStatus.paid
    purchase.purchased_at = datetime.now(UTC)
    purchase.ledger_transaction_id = ledger.id
    purchase.entitlement_id = entitlement.id
    await record_event(
        db,
        "purchase.settled",
        actor_user_id=purchase.buyer_user_id,
        target_type="purchase",
        target_id=str(purchase.id),
    )
    await emit_transactional(
        db,
        recipient_user_id=purchase.buyer_user_id,
        notification_type="PURCHASE_RECEIPT",
        source_domain="finance",
        source_id=str(purchase.id),
        title="Purchase receipt",
        body=f"Your purchase of {purchase.gross_amount_minor} {purchase.currency} is confirmed.",
        target_path="/purchases",
    )
    return purchase


async def reconcile_succeeded_payments(db: AsyncSession, limit: int = 100) -> int:
    """Recover payment successes whose webhook transaction stopped before settlement."""
    attempts = (
        await db.scalars(
            select(PaymentAttempt)
            .join(
                PurchasePaymentAttempt,
                PurchasePaymentAttempt.payment_attempt_id == PaymentAttempt.id,
            )
            .join(Purchase, Purchase.id == PurchasePaymentAttempt.purchase_id)
            .where(
                PaymentAttempt.status == PaymentStatus.succeeded,
                or_(
                    Purchase.status.in_([PurchaseStatus.awaiting_payment, PurchaseStatus.failed]),
                    and_(
                        Purchase.status == PurchaseStatus.paid,
                        Purchase.payment_attempt_id != PaymentAttempt.id,
                        ~exists(
                            select(PaymentRefundRequirement.id).where(
                                PaymentRefundRequirement.payment_attempt_id == PaymentAttempt.id
                            )
                        ),
                    ),
                ),
            )
            .order_by(PaymentAttempt.completed_at, PaymentAttempt.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    reconciled = 0
    for attempt in attempts:
        purchase = await _purchase_for_attempt(db, attempt.id)
        if purchase and await _settle_or_contain_purchase_success(db, purchase, attempt):
            reconciled += 1
    # Subscription renewals share PaymentAttempt but not Purchase.  Reconcile
    # their provider-confirmed success through the subscription settlement
    # idempotency key; this never initiates another provider charge.
    from app.models.subscription import SubscriptionPeriod, SubscriptionRenewalAttempt
    from app.subscriptions.service import settle_payment_attempt

    subscription_attempts = (
        await db.scalars(
            select(PaymentAttempt)
            .join(
                SubscriptionRenewalAttempt,
                SubscriptionRenewalAttempt.payment_attempt_id == PaymentAttempt.id,
            )
            .join(
                SubscriptionPeriod,
                SubscriptionPeriod.id == SubscriptionRenewalAttempt.subscription_period_id,
            )
            .where(
                PaymentAttempt.status == PaymentStatus.succeeded,
                or_(
                    and_(
                        SubscriptionPeriod.payment_attempt_id == PaymentAttempt.id,
                        SubscriptionPeriod.status.in_(["pending", "failed"]),
                    ),
                    and_(
                        SubscriptionPeriod.payment_attempt_id != PaymentAttempt.id,
                        ~exists(
                            select(PaymentRefundRequirement.id).where(
                                PaymentRefundRequirement.payment_attempt_id == PaymentAttempt.id
                            )
                        ),
                    ),
                ),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for attempt in subscription_attempts:
        subscription = await settle_payment_attempt(db, attempt)
        if subscription:
            reconciled += 1
    # Physical orders use the same provider-confirmed settlement boundary as
    # other paid domains.  Their frozen shipping snapshot is never recomputed.
    from app.marketplace.service import settle_or_contain_payment_attempt
    from app.models.marketplace import MarketplaceOrder, MarketplaceOrderStatus

    marketplace_attempts = (
        await db.scalars(
            select(PaymentAttempt)
            .join(MarketplaceOrder, MarketplaceOrder.payment_attempt_id == PaymentAttempt.id)
            .where(
                PaymentAttempt.status == PaymentStatus.succeeded,
                or_(
                    MarketplaceOrder.status == MarketplaceOrderStatus.awaiting_payment,
                    and_(
                        MarketplaceOrder.status == MarketplaceOrderStatus.cancelled,
                        ~exists(
                            select(PaymentRefundRequirement.id).where(
                                PaymentRefundRequirement.payment_attempt_id == PaymentAttempt.id
                            )
                        ),
                    ),
                ),
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for attempt in marketplace_attempts:
        order = await db.scalar(
            select(MarketplaceOrder).where(MarketplaceOrder.payment_attempt_id == attempt.id)
        )
        was_awaiting = bool(order and order.status is MarketplaceOrderStatus.awaiting_payment)
        if await settle_or_contain_payment_attempt(db, attempt) and was_awaiting:
            reconciled += 1
    return reconciled


async def creator_balances(db: AsyncSession, creator_id: UUID, currency: str) -> dict[str, int]:
    currency = currency_code(currency)
    rows = await db.execute(
        select(
            LedgerAccount.kind,
            func.coalesce(
                func.sum(
                    case(
                        (LedgerEntry.direction == LedgerDirection.credit, LedgerEntry.amount_minor),
                        else_=-LedgerEntry.amount_minor,
                    )
                ),
                0,
            ),
        )
        .join(LedgerEntry, LedgerEntry.ledger_account_id == LedgerAccount.id)
        .where(LedgerAccount.owner_creator_id == creator_id, LedgerAccount.currency == currency)
        .group_by(LedgerAccount.kind)
    )
    values = {kind.value: int(amount) for kind, amount in rows}
    return {
        "pending_amount_minor": values.get(LedgerAccountKind.creator_pending.value, 0),
        "available_amount_minor": values.get(LedgerAccountKind.creator_available.value, 0),
    }


async def creator_financial_summary(
    db: AsyncSession, creator_id: UUID, currency: str
) -> dict[str, int]:
    currency = currency_code(currency)
    balances = await creator_balances(db, creator_id, currency)
    result = await db.execute(
        select(
            func.coalesce(func.sum(Purchase.gross_amount_minor), 0),
            func.coalesce(func.sum(Purchase.platform_fee_minor), 0),
            func.coalesce(func.sum(Purchase.creator_amount_minor), 0),
        ).where(
            Purchase.seller_creator_id == creator_id,
            Purchase.currency == currency,
            Purchase.status == PurchaseStatus.paid,
        )
    )
    gross, fees, net = result.one()
    original_transaction = aliased(LedgerTransaction)
    marketplace_rows = await db.execute(
        select(
            LedgerTransaction.transaction_type,
            original_transaction.transaction_type,
            func.coalesce(
                func.sum(
                    case(
                        (LedgerEntry.direction == LedgerDirection.credit, LedgerEntry.amount_minor),
                        else_=-LedgerEntry.amount_minor,
                    )
                ),
                0,
            ),
        )
        .join(LedgerEntry, LedgerEntry.transaction_id == LedgerTransaction.id)
        .join(LedgerAccount, LedgerAccount.id == LedgerEntry.ledger_account_id)
        .outerjoin(
            original_transaction,
            original_transaction.id == LedgerTransaction.reversal_of_transaction_id,
        )
        .where(LedgerAccount.owner_creator_id == creator_id, LedgerAccount.currency == currency)
        .group_by(LedgerTransaction.transaction_type, original_transaction.transaction_type)
    )
    marketplace_net = 0
    for transaction_type, original_type, amount in marketplace_rows:
        source_type = (
            original_type
            if transaction_type.value in {"refund", "chargeback"} and original_type is not None
            else transaction_type
        )
        if source_type is LedgerTransactionType.marketplace_order:
            marketplace_net += int(amount)
    return {
        **balances,
        "ppv_gross_amount_minor": int(gross),
        "platform_fee_amount_minor": int(fees),
        "creator_net_amount_minor": int(net),
        "marketplace_net_amount_minor": marketplace_net,
    }


async def _disputed_creator_hold_amount(
    db: AsyncSession, creator_id: UUID, currency: str, pending_account_id: UUID
) -> int:
    """Return frozen creator allocations whose provider dispute is unresolved."""
    from app.models.messaging import MessageUnlockPurchase, PendingMessageSend
    from app.models.streaming import PrivateSession, PrivateSessionSettlement

    transaction_ids: set[UUID] = set(
        await db.scalars(
            select(Purchase.ledger_transaction_id).where(
                Purchase.seller_creator_id == creator_id,
                Purchase.currency == currency,
                Purchase.status == PurchaseStatus.disputed,
                Purchase.ledger_transaction_id.is_not(None),
            )
        )
    )
    transaction_ids.update(
        await db.scalars(
            select(SubscriptionPeriod.ledger_transaction_id)
            .join(Subscription, Subscription.id == SubscriptionPeriod.subscription_id)
            .join(PaymentAttempt, PaymentAttempt.id == SubscriptionPeriod.payment_attempt_id)
            .where(
                Subscription.creator_id == creator_id,
                SubscriptionPeriod.currency == currency,
                PaymentAttempt.status == PaymentStatus.disputed,
                SubscriptionPeriod.ledger_transaction_id.is_not(None),
            )
        )
    )
    transaction_ids.update(
        await db.scalars(
            select(MessageUnlockPurchase.ledger_transaction_id).where(
                MessageUnlockPurchase.seller_creator_id == creator_id,
                MessageUnlockPurchase.currency == currency,
                MessageUnlockPurchase.status == "disputed",
                MessageUnlockPurchase.ledger_transaction_id.is_not(None),
            )
        )
    )
    transaction_ids.update(
        await db.scalars(
            select(PendingMessageSend.ledger_transaction_id).where(
                PendingMessageSend.creator_id == creator_id,
                PendingMessageSend.currency == currency,
                PendingMessageSend.status == "disputed",
                PendingMessageSend.ledger_transaction_id.is_not(None),
            )
        )
    )
    transaction_ids.update(
        await db.scalars(
            select(PrivateSessionSettlement.ledger_transaction_id)
            .join(
                PrivateSession,
                PrivateSession.id == PrivateSessionSettlement.private_session_id,
            )
            .join(PaymentAttempt, PaymentAttempt.id == PrivateSession.payment_attempt_id)
            .where(
                PrivateSession.creator_id == creator_id,
                PrivateSessionSettlement.currency == currency,
                PaymentAttempt.status == PaymentStatus.disputed,
            )
        )
    )
    transaction_ids.discard(None)  # type: ignore[arg-type]
    if not transaction_ids:
        return 0
    return int(
        await db.scalar(
            select(func.coalesce(func.sum(LedgerEntry.amount_minor), 0)).where(
                LedgerEntry.transaction_id.in_(transaction_ids),
                LedgerEntry.ledger_account_id == pending_account_id,
                LedgerEntry.direction == LedgerDirection.credit,
            )
        )
        or 0
    )


async def release_creator_earnings(
    db: AsyncSession, creator_id: UUID, currency: str
) -> LedgerTransaction | None:
    settlement_seconds = get_settings().creator_earnings_settlement_seconds
    if settlement_seconds > 0:
        cutoff = datetime.now(UTC).timestamp() - settlement_seconds
        has_unsettled_purchase = await db.scalar(
            select(Purchase.id).where(
                Purchase.seller_creator_id == creator_id,
                Purchase.currency == currency,
                Purchase.status == PurchaseStatus.paid,
                Purchase.purchased_at > datetime.fromtimestamp(cutoff, UTC),
            )
        )
        if has_unsettled_purchase:
            return None
    pending = await _account(db, LedgerAccountKind.creator_pending, currency, creator_id)
    available = await _account(db, LedgerAccountKind.creator_available, currency, creator_id)
    balance = await db.scalar(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (LedgerEntry.direction == LedgerDirection.credit, LedgerEntry.amount_minor),
                        else_=-LedgerEntry.amount_minor,
                    )
                ),
                0,
            )
        ).where(LedgerEntry.ledger_account_id == pending.id)
    )
    if not balance or balance <= 0:
        return None
    # Physical marketplace allocations are not governed by the generic global
    # settlement window.  They remain in the same creator-pending account, but
    # are excluded here until their delivery-specific hold worker releases the
    # exact historical order allocation.
    from app.models.marketplace import (
        MarketplaceEarningsReleaseStatus,
        MarketplaceOrder,
        MarketplaceOrderStatus,
    )

    marketplace_held = await db.scalar(
        select(
            func.coalesce(
                func.sum(
                    MarketplaceOrder.creator_amount_minor
                    + MarketplaceOrder.shipping_pass_through_minor
                ),
                0,
            )
        ).where(
            MarketplaceOrder.seller_creator_id == creator_id,
            MarketplaceOrder.currency == currency,
            MarketplaceOrder.earnings_release_status != MarketplaceEarningsReleaseStatus.released,
            MarketplaceOrder.status.in_(
                [
                    MarketplaceOrderStatus.paid,
                    MarketplaceOrderStatus.processing,
                    MarketplaceOrderStatus.shipped,
                    MarketplaceOrderStatus.delivered,
                    MarketplaceOrderStatus.disputed,
                ]
            ),
        )
    )
    disputed_held = await _disputed_creator_hold_amount(db, creator_id, currency, pending.id)
    balance = int(balance) - int(marketplace_held or 0) - disputed_held
    if balance <= 0:
        return None
    release_number = await db.scalar(
        select(func.count())
        .select_from(LedgerTransaction)
        .where(_generic_creator_release_predicate(creator_id, currency))
    )
    release_key = f"release:{creator_id}:{currency}:{int(release_number or 0) + 1}"
    return await post_entries(
        db,
        transaction_type=LedgerTransactionType.earnings_release,
        currency=currency,
        idempotency_key=release_key,
        reference=release_key,
        entries=[
            (pending, LedgerDirection.debit, int(balance)),
            (available, LedgerDirection.credit, int(balance)),
        ],
        metadata={
            "creator_id": str(creator_id),
            "release_provenance": _GENERIC_CREATOR_RELEASE_PROVENANCE,
        },
    )


async def refund_purchase(
    db: AsyncSession, purchase: Purchase, actor: User, reason: str
) -> Purchase:
    attempt = await db.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == purchase.payment_attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if attempt is None:
        raise FinancialError("Purchase payment attempt is missing")
    purchase = await db.scalar(
        select(Purchase)
        .where(Purchase.id == purchase.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert purchase
    if purchase.payment_attempt_id != attempt.id:
        raise FinancialError("Purchase payment attempt changed; retry the refund command")
    if purchase.status is PurchaseStatus.refunded:
        return purchase
    if (
        purchase.status not in {PurchaseStatus.paid, PurchaseStatus.disputed}
        or not purchase.ledger_transaction_id
    ):
        raise FinancialError("Only settled purchases can be refunded")
    entitlement = await db.get(ContentEntitlement, purchase.entitlement_id)
    if not entitlement:
        raise FinancialError("Purchase entitlement is missing")
    refund = await reverse_original_ledger(
        db,
        purchase.ledger_transaction_id,
        transaction_type=LedgerTransactionType.refund,
        idempotency_key=f"refund:{purchase.id}",
        reference=f"refund:{purchase.id}",
        metadata={
            "purchase_id": str(purchase.id),
            "reason": reason,
        },
    )
    purchase.status = PurchaseStatus.refunded
    entitlement.status = EntitlementStatus.revoked
    attempt.status = PaymentStatus.refunded
    await record_event(
        db,
        "purchase.refunded",
        actor_user_id=actor.id,
        target_type="purchase",
        target_id=str(purchase.id),
        metadata={"refund_transaction_id": str(refund.id), "reason": reason},
    )
    await emit_transactional(
        db,
        recipient_user_id=purchase.buyer_user_id,
        notification_type="REFUND_ISSUED",
        source_domain="finance",
        source_id=str(refund.id),
        title="Refund issued",
        body=f"A refund of {purchase.gross_amount_minor} {purchase.currency} was issued.",
        target_path="/purchases",
    )
    return purchase


async def refund_message_charge(db: AsyncSession, charge, actor: User, reason: str):
    """Reverse a settled paid send or attachment unlock without deleting its message history."""
    attempt = await db.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == charge.payment_attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if attempt is None:
        raise FinancialError("Messaging payment attempt is missing")
    charge = await db.scalar(
        select(type(charge))
        .where(type(charge).id == charge.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if charge is None:
        raise FinancialError("Messaging charge not found")
    if charge.payment_attempt_id != attempt.id:
        raise FinancialError("Messaging payment attempt changed; retry the refund command")
    if charge.status == "chargeback":
        raise FinancialError("A chargeback cannot be downgraded to a refund")
    if charge.status == "refunded":
        return charge
    if charge.status not in {"paid", "disputed"} or not charge.ledger_transaction_id:
        raise FinancialError("Only settled messaging charges can be refunded")
    refund = await reverse_original_ledger(
        db,
        charge.ledger_transaction_id,
        transaction_type=LedgerTransactionType.refund,
        idempotency_key=f"messaging_refund:{charge.id}",
        reference=f"messaging_refund:{charge.id}",
        metadata={"messaging_charge_id": str(charge.id), "reason": reason},
    )
    charge.status = "refunded"
    attempt.status = PaymentStatus.refunded
    await record_event(
        db,
        "messaging.refunded",
        actor_user_id=actor.id,
        target_type=type(charge).__tablename__,
        target_id=str(charge.id),
        metadata={"refund_transaction_id": str(refund.id), "reason": reason},
    )
    return charge


async def refund_subscription_period(
    db: AsyncSession, period: SubscriptionPeriod, actor: User, reason: str
) -> SubscriptionPeriod:
    """Reverse one settled subscription period and revoke only its entitlement.

    A refund is a new immutable ledger transaction.  It deliberately does not
    delete or rewrite the commercial snapshot or its original charge.
    """
    attempt = await db.scalar(
        select(PaymentAttempt)
        .where(PaymentAttempt.id == period.payment_attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if attempt is None:
        raise FinancialError("Subscription payment attempt is missing")
    period = await db.scalar(
        select(SubscriptionPeriod)
        .where(SubscriptionPeriod.id == period.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert period
    if period.payment_attempt_id != attempt.id:
        raise FinancialError("Subscription payment attempt changed; retry the refund command")
    if period.status is SubscriptionPeriodStatus.chargeback:
        raise FinancialError("A chargeback cannot be downgraded to a refund")
    if period.status is SubscriptionPeriodStatus.refunded:
        return period
    if (
        period.status not in {SubscriptionPeriodStatus.active, SubscriptionPeriodStatus.disputed}
        or not period.ledger_transaction_id
    ):
        raise FinancialError("Only settled subscription periods can be refunded")
    subscription = await db.scalar(
        select(Subscription).where(Subscription.id == period.subscription_id).with_for_update()
    )
    assert subscription
    entitlement = await db.get(ContentEntitlement, period.entitlement_id)
    if not entitlement:
        raise FinancialError("Subscription entitlement is missing")
    refund = await reverse_original_ledger(
        db,
        period.ledger_transaction_id,
        transaction_type=LedgerTransactionType.refund,
        idempotency_key=f"subscription-refund:{period.id}",
        reference=f"subscription_refund:{period.id}",
        metadata={"subscription_period_id": str(period.id), "reason": reason},
    )
    period.status = SubscriptionPeriodStatus.refunded
    entitlement.status = EntitlementStatus.revoked
    entitlement.valid_until = datetime.now(UTC)
    attempt.status = PaymentStatus.refunded
    # Historical periods are independent.  Only a refund of the currently
    # authoritative period ends the logical subscription and its future renewal.
    if (
        subscription.current_period_start == period.period_start
        and subscription.current_period_end == period.period_end
    ):
        subscription.status = SubscriptionStatus.expired
        subscription.auto_renew = False
        subscription.cancel_at_period_end = True
        subscription.ended_at = datetime.now(UTC)
    await record_event(
        db,
        "subscription.period_refunded",
        actor_user_id=actor.id,
        target_type="subscription_period",
        target_id=str(period.id),
        metadata={"refund_transaction_id": str(refund.id), "reason": reason},
    )
    return period
