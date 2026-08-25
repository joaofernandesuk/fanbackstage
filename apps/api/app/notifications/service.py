import hashlib
import html
import json
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.service import record_event
from app.core.config import get_settings
from app.integrations.email import email_provider
from app.models.identity import User
from app.models.notification import (
    DeliveryStatus,
    EmailSuppression,
    InAppNotification,
    NotificationChannel,
    NotificationClass,
    NotificationDeliveryAttempt,
    NotificationIntent,
    NotificationPreference,
    NotificationPriority,
    SuppressionReason,
)

MANDATORY_TYPES = {
    "AUTH_EMAIL_VERIFICATION",
    "AUTH_PASSWORD_RESET",
    "SECURITY_PASSWORD_CHANGED",
    "SECURITY_EMAIL_CHANGED",
    "PURCHASE_RECEIPT",
    "REFUND_ISSUED",
    "CHARGEBACK_NOTICE",
    "MODERATION_ACTION",
    "APPEAL_DECIDED",
}
TYPE_CATEGORIES = {
    "AUTH_EMAIL_VERIFICATION": "account_security",
    "AUTH_PASSWORD_RESET": "account_security",
    "SECURITY_PASSWORD_CHANGED": "account_security",
    "SECURITY_EMAIL_CHANGED": "account_security",
    "PURCHASE_RECEIPT": "purchases",
    "REFUND_ISSUED": "purchases",
    "SUBSCRIPTION_STARTED": "subscriptions",
    "SUBSCRIPTION_RENEWED": "subscriptions",
    "SUBSCRIPTION_CANCELLED": "subscriptions",
    "MARKETPLACE_ORDER_PLACED": "marketplace",
    "MARKETING": "marketing",
}


def _now() -> datetime:
    return datetime.now(UTC)


def email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()


def _cipher() -> Fernet:
    import base64

    key = base64.urlsafe_b64encode(hashlib.sha256(get_settings().session_secret.encode()).digest())
    return Fernet(key)


def _safe_path(path: str | None) -> str | None:
    if not path:
        return None
    parsed = urlparse(path)
    if parsed.scheme or parsed.netloc or not path.startswith("/") or path.startswith("//"):
        raise ValueError("Notification target must be an internal path")
    return path


async def create_intent(
    db: AsyncSession,
    *,
    recipient_user_id: UUID,
    notification_type: str,
    classification: NotificationClass,
    source_domain: str,
    source_id: str,
    payload: dict,
    channels: tuple[NotificationChannel, ...] = (
        NotificationChannel.email,
        NotificationChannel.in_app,
    ),
    priority: NotificationPriority | None = None,
    secure_payload: dict | None = None,
) -> NotificationIntent:
    key = f"{notification_type}:{source_domain}:{source_id}:{recipient_user_id}"
    existing = await db.scalar(
        select(NotificationIntent).where(NotificationIntent.idempotency_key == key)
    )
    if existing:
        return existing
    if classification is NotificationClass.marketing and notification_type in MANDATORY_TYPES:
        raise ValueError("Mandatory notification cannot be marketing")
    intent = NotificationIntent(
        recipient_user_id=recipient_user_id,
        notification_type=notification_type,
        classification=classification,
        priority=priority
        or (
            NotificationPriority.marketing
            if classification is NotificationClass.marketing
            else NotificationPriority.transactional
        ),
        source_domain=source_domain,
        source_id=source_id,
        idempotency_key=key,
        payload_json=payload,
        secure_payload=_cipher().encrypt(json.dumps(secure_payload).encode()).decode()
        if secure_payload
        else None,
    )
    db.add(intent)
    await db.flush()
    if NotificationChannel.in_app in channels:
        db.add(
            InAppNotification(
                intent_id=intent.id,
                recipient_user_id=recipient_user_id,
                notification_type=notification_type,
                title=html.escape(str(payload.get("title", "FanBackstage notification"))),
                body=html.escape(str(payload.get("body", "You have a new notification."))),
                target_path=_safe_path(payload.get("target_path")),
            )
        )
    return intent


async def _eligible(db: AsyncSession, intent: NotificationIntent, user: User) -> bool:
    globally_suppressed = await db.scalar(
        select(EmailSuppression.id).where(
            EmailSuppression.email_hash == email_hash(user.email),
            EmailSuppression.marketing_only.is_(False),
        )
    )
    if globally_suppressed:
        return False
    if intent.classification is NotificationClass.transactional:
        return True
    if not user.is_active:
        return False
    pref = await db.scalar(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user.id,
            NotificationPreference.category == "marketing",
        )
    )
    if not pref or not pref.email_enabled or not pref.consented_at:
        return False
    return not bool(
        await db.scalar(
            select(EmailSuppression.id).where(EmailSuppression.email_hash == email_hash(user.email))
        )
    )


async def deliver_intent(db: AsyncSession, intent_id: UUID) -> DeliveryStatus:
    intent = await db.get(NotificationIntent, intent_id)
    if not intent:
        return DeliveryStatus.failed_permanent
    user = await db.get(User, intent.recipient_user_id)
    if not user:
        return DeliveryStatus.failed_permanent
    previous = await db.scalar(
        select(NotificationDeliveryAttempt)
        .where(
            NotificationDeliveryAttempt.intent_id == intent.id,
            NotificationDeliveryAttempt.channel == NotificationChannel.email,
            NotificationDeliveryAttempt.status.in_([DeliveryStatus.sent, DeliveryStatus.delivered]),
        )
        .limit(1)
    )
    if previous:
        return previous.status
    number = (
        int(
            await db.scalar(
                select(func.count(NotificationDeliveryAttempt.id)).where(
                    NotificationDeliveryAttempt.intent_id == intent.id
                )
            )
        )
        + 1
    )
    attempt = NotificationDeliveryAttempt(
        intent_id=intent.id,
        channel=NotificationChannel.email,
        status=DeliveryStatus.processing,
        attempt_number=number,
        provider=email_provider.name,
        recipient_snapshot=user.email,
        template_key=intent.notification_type,
        template_version=1,
    )
    db.add(attempt)
    if not await _eligible(db, intent, user):
        attempt.status = DeliveryStatus.suppressed
        return attempt.status
    secure = (
        json.loads(_cipher().decrypt(intent.secure_payload.encode()))
        if intent.secure_payload
        else {}
    )
    try:
        attempt.provider_message_id = await email_provider.send(
            template=intent.notification_type,
            recipient=user.email,
            payload=intent.payload_json,
            secure_payload=secure,
            classification=intent.classification.value,
            idempotency_key=intent.idempotency_key,
        )
        attempt.status = DeliveryStatus.sent
    except (TimeoutError, ConnectionError):
        attempt.status = DeliveryStatus.failed_retryable
        attempt.error_code = "transient_provider_error"
    except Exception:  # noqa: BLE001 - provider adapters deliberately normalize failures here.
        attempt.status = DeliveryStatus.failed_permanent
        attempt.error_code = "provider_rejected"
    return attempt.status


async def update_preference(
    db: AsyncSession,
    user: User,
    category: str,
    email_enabled: bool,
    in_app_enabled: bool,
    consent: bool = False,
) -> NotificationPreference:
    if category == "account_security":
        raise ValueError("Mandatory account security notifications cannot be disabled")
    pref = await db.scalar(
        select(NotificationPreference).where(
            NotificationPreference.user_id == user.id, NotificationPreference.category == category
        )
    )
    if not pref:
        pref = NotificationPreference(user_id=user.id, category=category)
        db.add(pref)
    pref.email_enabled, pref.in_app_enabled = email_enabled, in_app_enabled
    if category == "marketing":
        pref.consented_at = _now() if consent and email_enabled else None
        pref.consent_source = "account_settings" if pref.consented_at else None
    await record_event(
        db,
        "notification.preference_changed",
        actor_user_id=user.id,
        target_type="notification_preference",
        target_id=str(user.id),
        metadata={
            "category": category,
            "email_enabled": email_enabled,
            "in_app_enabled": in_app_enabled,
        },
    )
    return pref


async def unsubscribe(db: AsyncSession, user: User) -> None:
    await update_preference(db, user, "marketing", False, True)
    if not await db.scalar(
        select(EmailSuppression).where(
            EmailSuppression.email_hash == email_hash(user.email),
            EmailSuppression.reason == SuppressionReason.marketing_unsubscribe,
        )
    ):
        db.add(
            EmailSuppression(
                email_hash=email_hash(user.email),
                reason=SuppressionReason.marketing_unsubscribe,
                marketing_only=True,
                source="unsubscribe",
            )
        )


async def mark_provider_event(db: AsyncSession, provider_message_id: str, event: str) -> bool:
    attempt = await db.scalar(
        select(NotificationDeliveryAttempt).where(
            NotificationDeliveryAttempt.provider_message_id == provider_message_id
        )
    )
    if not attempt:
        return False
    if event == "delivered":
        attempt.status = DeliveryStatus.delivered
    elif event in {"hard_bounce", "complaint"}:
        attempt.status = DeliveryStatus.failed_permanent
        if attempt.recipient_snapshot and not await db.scalar(
            select(EmailSuppression.id).where(
                EmailSuppression.email_hash == email_hash(attempt.recipient_snapshot),
                EmailSuppression.reason == SuppressionReason(event),
            )
        ):
            db.add(
                EmailSuppression(
                    email_hash=email_hash(attempt.recipient_snapshot),
                    reason=SuppressionReason(event),
                    marketing_only=False,
                    source="provider_webhook",
                )
            )
    return True
