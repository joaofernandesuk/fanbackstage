"""Transaction-scoped serialization for subject compliance authority changes."""

from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


def _subject_lock_key(user_id: UUID) -> int:
    # Fold the UUID to one positive signed-bigint key. The dedicated high-bit
    # namespace prevents accidental overlap with unrelated advisory-lock uses;
    # a 62-bit subject space keeps collision risk negligible while PostgreSQL
    # remains the explicit persistence/runtime contract.
    folded = (user_id.int ^ (user_id.int >> 64)) & ((1 << 62) - 1)
    return (1 << 62) | folded


async def lock_compliance_subjects(
    db: AsyncSession,
    user_ids: Iterable[UUID],
) -> None:
    """Acquire deterministic per-user authority locks for the transaction.

    Token/join mutations and verification lifecycle writes use the same keys.
    Sorting makes multi-participant private-session acquisition deadlock-safe.
    """

    for user_id in sorted(set(user_ids), key=lambda value: value.int):
        await db.execute(select(func.pg_advisory_xact_lock(_subject_lock_key(user_id))))


async def lock_compliance_subject(db: AsyncSession, user_id: UUID) -> None:
    await lock_compliance_subjects(db, (user_id,))
