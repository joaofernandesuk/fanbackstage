from celery import Celery
from kombu import Queue

from app.core.config import get_settings

celery_app = Celery(
    "fanbackstage",
    broker=get_settings().redis_url,
    backend=get_settings().redis_url,
    include=["app.worker.tasks"],
)
celery_app.conf.task_default_queue = "default"
celery_app.conf.task_queues = tuple(
    Queue(name)
    for name in (
        "default",
        "media",
        "notifications",
        "notifications_marketing",
        "moderation",
        "analytics",
        "financial",
        "scheduled",
    )
)
celery_app.conf.task_routes = {
    "app.worker.tasks.process_media_asset": {"queue": "media"},
    "app.worker.tasks.reconcile_financial_settlement": {"queue": "financial"},
    "app.worker.tasks.process_subscription_renewals": {"queue": "scheduled"},
    "app.worker.tasks.finalize_subscription_expirations": {"queue": "scheduled"},
    "app.worker.tasks.retry_subscription_renewals": {"queue": "scheduled"},
    "app.worker.tasks.publish_scheduled_posts": {"queue": "scheduled"},
    "app.worker.tasks.process_scheduled_mass_messages": {"queue": "scheduled"},
    "app.worker.tasks.reconcile_private_session_grace": {"queue": "scheduled"},
    "app.worker.tasks.release_marketplace_earnings": {"queue": "financial"},
    "app.worker.tasks.expire_marketplace_reservations": {"queue": "scheduled"},
    "app.worker.tasks.reconcile_featuring_lifecycle": {"queue": "scheduled"},
    "app.worker.tasks.expire_consent_releases": {"queue": "scheduled"},
    "app.worker.tasks.deliver_notification": {"queue": "notifications"},
    "app.worker.tasks.reconcile_notification_delivery": {"queue": "notifications"},
    "app.worker.tasks.*": {"queue": "default"},
}
celery_app.conf.beat_schedule = {
    "subscription-renewals": {
        "task": "app.worker.tasks.process_subscription_renewals",
        "schedule": 300.0,
    },
    "subscription-expirations": {
        "task": "app.worker.tasks.finalize_subscription_expirations",
        "schedule": 300.0,
    },
    "subscription-renewal-retries": {
        "task": "app.worker.tasks.retry_subscription_renewals",
        "schedule": 300.0,
    },
    "financial-settlement-reconciliation": {
        "task": "app.worker.tasks.reconcile_financial_settlement",
        "schedule": 300.0,
    },
    "scheduled-feed-posts": {
        "task": "app.worker.tasks.publish_scheduled_posts",
        "schedule": 60.0,
    },
    "scheduled-mass-messages": {
        "task": "app.worker.tasks.process_scheduled_mass_messages",
        "schedule": 60.0,
    },
    "private-session-reconnect-grace": {
        "task": "app.worker.tasks.reconcile_private_session_grace",
        "schedule": 10.0,
    },
    "marketplace-earnings-release": {
        "task": "app.worker.tasks.release_marketplace_earnings",
        "schedule": 300.0,
    },
    "marketplace-reservation-expiry": {
        "task": "app.worker.tasks.expire_marketplace_reservations",
        "schedule": 60.0,
    },
    "featuring-lifecycle": {
        "task": "app.worker.tasks.reconcile_featuring_lifecycle",
        "schedule": 60.0,
    },
    "consent-release-expiry": {
        "task": "app.worker.tasks.expire_consent_releases",
        "schedule": 60.0,
    },
    "notification-delivery-reconciliation": {
        "task": "app.worker.tasks.reconcile_notification_delivery",
        "schedule": 60.0,
    },
}
