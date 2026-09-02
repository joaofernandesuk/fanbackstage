import logging

from celery import Celery
from celery.signals import task_failure
from kombu import Queue

from app.core.config import get_settings
from app.core.logging import configure_sensitive_http_logging, configure_structured_logging

configure_structured_logging(
    service="fanbackstage-worker",
    environment=get_settings().environment,
)
configure_sensitive_http_logging()
logger = logging.getLogger("fanbackstage.worker")


@task_failure.connect
def log_task_failure(sender=None, task_id=None, exception=None, **_kwargs) -> None:
    """Emit a query/payload-free failure signal for every operational domain queue."""

    logger.error(
        "celery_task_failed",
        extra={
            "event_id": task_id,
            "error_type": type(exception).__name__ if exception else "UnknownError",
            "metrics": {"task": getattr(sender, "name", "unknown"), "status": "failed"},
        },
    )


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
        "live_control",
    )
)
celery_app.conf.task_routes = {
    "app.worker.tasks.process_media_asset": {"queue": "media"},
    "app.worker.tasks.reconcile_financial_settlement": {"queue": "financial"},
    "app.worker.tasks.process_subscription_renewals": {"queue": "scheduled"},
    "app.worker.tasks.finalize_subscription_expirations": {"queue": "scheduled"},
    "app.worker.tasks.retry_subscription_renewals": {"queue": "scheduled"},
    "app.worker.tasks.publish_scheduled_posts": {"queue": "scheduled"},
    "app.worker.tasks.expire_stories": {"queue": "scheduled"},
    "app.worker.tasks.process_scheduled_mass_messages": {"queue": "scheduled"},
    "app.worker.tasks.reconcile_private_session_grace": {
        "queue": "live_control" if get_settings().environment == "development" else "scheduled"
    },
    "app.worker.tasks.expire_live_paid_requests": {"queue": "scheduled"},
    "app.worker.tasks.release_marketplace_earnings": {"queue": "financial"},
    "app.worker.tasks.expire_marketplace_reservations": {"queue": "scheduled"},
    "app.worker.tasks.reconcile_featuring_lifecycle": {"queue": "scheduled"},
    "app.worker.tasks.expire_consent_releases": {"queue": "scheduled"},
    "app.worker.tasks.reconcile_age_verifications": {"queue": "scheduled"},
    "app.worker.tasks.process_live_provider_control_outbox": {
        # Native macOS LiveKit is loopback-only. In development a small
        # host-native worker owns this queue; deployed environments keep the
        # task on the normal scheduled worker.
        "queue": "live_control" if get_settings().environment == "development" else "scheduled"
    },
    "app.worker.tasks.reconcile_legal_acceptance_notifications": {"queue": "scheduled"},
    "app.worker.tasks.deliver_notification": {"queue": "notifications"},
    "app.worker.tasks.reconcile_notification_delivery": {"queue": "notifications"},
    "app.worker.tasks.record_operations_heartbeat": {"queue": "scheduled"},
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
    "story-expiry": {
        "task": "app.worker.tasks.expire_stories",
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
    "live-paid-request-expiry": {
        "task": "app.worker.tasks.expire_live_paid_requests",
        "schedule": 30.0,
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
    "age-verification-lifecycle": {
        "task": "app.worker.tasks.reconcile_age_verifications",
        # Connected LiveKit clients are not disconnected by JWT expiry. Keep
        # authority-expiry discovery within the same bounded recovery cadence
        # as committed provider-control processing.
        "schedule": 10.0,
    },
    "live-provider-control-outbox": {
        "task": "app.worker.tasks.process_live_provider_control_outbox",
        "schedule": 10.0,
    },
    "legal-acceptance-notifications": {
        "task": "app.worker.tasks.reconcile_legal_acceptance_notifications",
        "schedule": 10.0,
    },
    "notification-delivery-reconciliation": {
        "task": "app.worker.tasks.reconcile_notification_delivery",
        "schedule": 60.0,
    },
    "operations-heartbeat": {
        "task": "app.worker.tasks.record_operations_heartbeat",
        "schedule": 30.0,
    },
}
