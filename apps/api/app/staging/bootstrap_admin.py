from __future__ import annotations

import argparse
import asyncio
import secrets
from datetime import UTC, datetime

from sqlalchemy import select

from app.accounts import service as accounts
from app.audit.service import record_event
from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.identity import TokenPurpose, User
from app.models.notification import (
    NotificationChannel,
    NotificationClass,
    NotificationPriority,
)
from app.notifications.service import create_intent

CONFIRMATION = "BOOTSTRAP-STAGING-ADMIN"


async def bootstrap_admin(email: str, confirmation: str) -> tuple[User, str]:
    settings = get_settings()
    if settings.environment != "staging" or confirmation != CONFIRMATION:
        raise RuntimeError("Staging admin bootstrap refused")
    normalized = email.strip().lower()
    if not normalized or "@" not in normalized:
        raise ValueError("A nominated operator email is required")
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.email == normalized))
        if user is None:
            user = User(
                email=normalized,
                password_hash=accounts.password_hash.hash(secrets.token_urlsafe(48)),
                email_verified_at=datetime.now(UTC),
                is_active=True,
            )
            db.add(user)
            await db.flush()
        await accounts.assign_role(db, user, "admin", None, "staging-admin-bootstrap")
        await accounts.assign_role(db, user, "super_admin", None, "staging-admin-bootstrap")
        token = await accounts.issue_security_token(db, user.id, TokenPurpose.password_reset)
        intent = await create_intent(
            db,
            recipient_user_id=user.id,
            notification_type="AUTH_PASSWORD_RESET",
            classification=NotificationClass.transactional,
            priority=NotificationPriority.critical_security,
            source_domain="staging_admin_bootstrap",
            source_id=accounts._digest(token),
            payload={
                "subject": "Set your FanBackstage staging administrator password",
                "body": "Use this one-time link to complete the staging administrator setup.",
            },
            channels=(NotificationChannel.email,),
            secure_payload={"path": "/reset-password", "token": token},
        )
        await record_event(
            db,
            "staging.admin_bootstrap_requested",
            target_type="user",
            target_id=str(user.id),
            correlation_id="staging-admin-bootstrap",
            metadata={"roles": ["admin", "super_admin"]},
        )
        await db.commit()
    from app.worker.tasks import deliver_notification

    deliver_notification.delay(str(intent.id))
    return user, str(intent.id)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap one private-staging super administrator"
    )
    parser.add_argument("--email", required=True)
    parser.add_argument("--confirm", required=True)
    args = parser.parse_args()
    user, intent_id = asyncio.run(bootstrap_admin(args.email, args.confirm))
    print(f"Staging administrator setup queued for {user.email}; intent={intent_id}")


if __name__ == "__main__":
    main()
