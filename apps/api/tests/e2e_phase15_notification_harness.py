"""Isolated release-validation harness for Phase 15 notification journeys.

It enters the notification module only at its public domain-service boundary and
uses the real Celery worker for dispatch.  It is deliberately unavailable to
the application itself and refuses to run unless explicitly marked as an
isolated E2E invocation.
"""

import asyncio
import json
import os
import sys

from sqlalchemy import func, select

if os.environ.get("FANBACKSTAGE_E2E_RELEASE_VALIDATION") != "1":
    raise SystemExit("Phase 15 release harness requires isolated E2E validation")

from app.db.session import SessionLocal
from app.models.finance import Purchase
from app.models.identity import User
from app.models.notification import (
    EmailSuppression,
    NotificationChannel,
    NotificationClass,
    NotificationDeliveryAttempt,
    NotificationIntent,
)
from app.notifications import service
from app.worker.tasks import deliver_notification


async def main() -> dict:
    command, *args = sys.argv[1:]
    async with SessionLocal() as db:
        if command == "queue":
            email, kind, source_id = args
            user = await db.scalar(select(User).where(User.email == email))
            if not user:
                raise ValueError("Validation recipient not found")
            classification = NotificationClass(kind)
            intent = await service.create_intent(
                db,
                recipient_user_id=user.id,
                notification_type="MARKETING"
                if classification is NotificationClass.marketing
                else "PURCHASE_RECEIPT",
                classification=classification,
                source_domain="phase15_release_validation",
                source_id=source_id,
                payload={
                    "subject": f"Phase 15 {kind} {source_id}",
                    "body": f"Phase 15 release-validation {kind} message {source_id}.",
                },
                channels=(NotificationChannel.email,),
            )
            await db.commit()
            return {"intent_id": str(intent.id), "source_id": source_id}
        if command == "enqueue":
            intent_id = args[0]
            deliver_notification.delay(intent_id)
            return {"intent_id": intent_id, "enqueued": True}
        if command == "inspect":
            intent_id = args[0]
            intent = await db.get(NotificationIntent, intent_id)
            attempts = (
                await db.scalars(
                    select(NotificationDeliveryAttempt)
                    .where(NotificationDeliveryAttempt.intent_id == intent_id)
                    .order_by(NotificationDeliveryAttempt.attempt_number)
                )
            ).all()
            return {
                "intent_count": int(
                    await db.scalar(
                        select(func.count(NotificationIntent.id)).where(
                            NotificationIntent.id == intent_id
                        )
                    )
                ),
                "attempts": [
                    {
                        "status": attempt.status.value,
                        "provider_message_id": attempt.provider_message_id,
                        "recipient": attempt.recipient_snapshot,
                    }
                    for attempt in attempts
                ],
                "payload": intent.payload_json if intent else None,
            }
        if command == "receipt":
            email, source_id = args
            user = await db.scalar(select(User).where(User.email == email))
            rows = (
                await db.scalars(
                    select(NotificationIntent).where(
                        NotificationIntent.recipient_user_id == user.id,
                        NotificationIntent.notification_type == "PURCHASE_RECEIPT",
                        NotificationIntent.source_domain == "finance",
                        NotificationIntent.source_id == source_id,
                    )
                )
            ).all()
            attempts = []
            if rows:
                attempts = (
                    await db.scalars(
                        select(NotificationDeliveryAttempt).where(
                            NotificationDeliveryAttempt.intent_id == rows[0].id
                        )
                    )
                ).all()
            return {
                "intent_count": len(rows),
                "payload": rows[0].payload_json if rows else None,
                "attempt_count": len(attempts),
                "statuses": [attempt.status.value for attempt in attempts],
                "payment_attempt_id": str((await db.get(Purchase, source_id)).payment_attempt_id),
            }
        if command == "unsubscribe-token":
            email = args[0]
            user = await db.scalar(select(User).where(User.email == email))
            if not user:
                raise ValueError("Validation recipient not found")
            return {"token": service.unsubscribe_token(user.id)}
        if command == "suppression-count":
            email = args[0]
            return {
                "count": int(
                    await db.scalar(
                        select(func.count(EmailSuppression.id)).where(
                            EmailSuppression.email_hash == service.email_hash(email)
                        )
                    )
                )
            }
        if command == "make-ineligible":
            email = args[0]
            user = await db.scalar(select(User).where(User.email == email))
            if not user:
                raise ValueError("Validation recipient not found")
            # The release harness models the existing account-ineligible state;
            # it does not add an application endpoint or change production rules.
            user.is_active = False
            await db.commit()
            return {"ineligible": True}
    raise ValueError("Unknown validation command")


print(json.dumps(asyncio.run(main())))
