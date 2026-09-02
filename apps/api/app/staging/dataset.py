from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select

from app.accounts import service as accounts
from app.audit.service import record_event
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.creator import (
    CreatorProfile,
    CreatorStatus,
    CreatorVerification,
    VerificationStatus,
)
from app.models.identity import User

FAN_PERSONAS = (
    "fan-payment-success@staging-test.invalid",
    "fan-payment-decline@staging-test.invalid",
    "fan-ppv-refund@staging-test.invalid",
)
CREATOR_PERSONAS = {
    "creator-kyc-not-started@staging-test.invalid": None,
    "creator-kyc-pending@staging-test.invalid": VerificationStatus.pending,
    "creator-kyc-verified@staging-test.invalid": VerificationStatus.verified,
    "creator-kyc-failed@staging-test.invalid": VerificationStatus.failed,
    "creator-kyc-review-required@staging-test.invalid": VerificationStatus.needs_review,
}
DATASET_EMAILS = FAN_PERSONAS + tuple(CREATOR_PERSONAS)
RESET_CONFIRMATION = "RESET-STAGING-TEST-DATA"


def _assert_enabled() -> None:
    settings = get_settings()
    if settings.environment != "staging" or not settings.staging_dataset_enabled:
        raise RuntimeError("The fictional dataset is limited to explicitly enabled staging")


async def create_dataset(credentials_path: Path) -> int:
    _assert_enabled()
    descriptor = os.open(credentials_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    credentials: dict[str, str] = {}
    try:
        async with SessionLocal() as db:
            users: dict[str, User] = {}
            for email in DATASET_EMAILS:
                user = await db.scalar(select(User).where(User.email == email))
                if user is None:
                    password = secrets.token_urlsafe(32)
                    user, _ = await accounts.register(
                        db,
                        email,
                        password,
                        "staging-dataset",
                        country_code=get_settings().effective_compliance_fallback_country(),
                    )
                    credentials[email] = password
                user.email_verified_at = user.email_verified_at or datetime.now(UTC)
                users[email] = user

            for email, expected_status in CREATOR_PERSONAS.items():
                user = users[email]
                await accounts.assign_role(db, user, "creator", None, "staging-dataset")
                profile = await db.scalar(
                    select(CreatorProfile).where(CreatorProfile.user_id == user.id)
                )
                local_part = email.split("@", 1)[0]
                if profile is None:
                    profile = CreatorProfile(
                        user_id=user.id,
                        username=local_part,
                        display_name=local_part.replace("-", " ").title(),
                        bio="Fictional staging-only account.",
                        country_code=user.country_code,
                        status=(
                            CreatorStatus.pending_review
                            if expected_status is VerificationStatus.verified
                            else CreatorStatus.pending_verification
                        ),
                        is_public=False,
                    )
                    db.add(profile)
                    await db.flush()
                if expected_status is None:
                    continue
                reference = f"staging-dataset-{local_part}"
                verification = await db.scalar(
                    select(CreatorVerification).where(
                        CreatorVerification.provider_reference == reference
                    )
                )
                if verification is None:
                    now = datetime.now(UTC)
                    verified = expected_status is VerificationStatus.verified
                    verification = CreatorVerification(
                        creator_profile_id=profile.id,
                        provider="staging_sandbox",
                        provider_reference=reference,
                        status=expected_status,
                        identity_verified=verified,
                        adult_verified=verified,
                        country_code=user.country_code,
                        verified_at=now if verified else None,
                        expires_at=(now + timedelta(days=90)) if verified else None,
                        failure_reason_code=(
                            None
                            if verified
                            else "MANUAL_REVIEW_REQUIRED"
                            if expected_status is VerificationStatus.needs_review
                            else expected_status.value.upper()
                        ),
                        metadata_json={"sandbox": "staging_test_only"},
                    )
                    db.add(verification)
            await record_event(
                db,
                "staging.dataset_created",
                target_type="staging_dataset",
                target_id="private-staging-v1",
                correlation_id="staging-dataset",
                metadata={"fictional_account_count": len(DATASET_EMAILS)},
            )
            await db.commit()
        with os.fdopen(descriptor, "w") as output:
            json.dump(credentials, output, indent=2, sort_keys=True)
            output.write("\n")
        descriptor = -1
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        credentials_path.unlink(missing_ok=True)
        raise
    return len(credentials)


async def reset_dataset(confirmation: str) -> int:
    _assert_enabled()
    if confirmation != RESET_CONFIRMATION:
        raise RuntimeError("Staging dataset reset confirmation is invalid")
    async with SessionLocal() as db:
        users = list((await db.scalars(select(User).where(User.email.in_(DATASET_EMAILS)))).all())
        await record_event(
            db,
            "staging.dataset_reset",
            target_type="staging_dataset",
            target_id="private-staging-v1",
            correlation_id="staging-dataset-reset",
            metadata={"fictional_account_count": len(users)},
        )
        await db.execute(delete(User).where(User.email.in_(DATASET_EMAILS)))
        await db.commit()
    return len(users)


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage the private-staging fictional dataset")
    subcommands = parser.add_subparsers(dest="command", required=True)
    create = subcommands.add_parser("create")
    create.add_argument("--credentials-file", required=True, type=Path)
    reset = subcommands.add_parser("reset")
    reset.add_argument("--confirm", required=True)
    args = parser.parse_args()
    if args.command == "create":
        count = asyncio.run(create_dataset(args.credentials_file))
        print(f"Created {count} fictional staging accounts; credentials written mode 0600")
    else:
        count = asyncio.run(reset_dataset(args.confirm))
        print(f"Removed {count} fictional staging accounts")


if __name__ == "__main__":
    main()
