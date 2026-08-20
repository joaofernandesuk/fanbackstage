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
        "moderation",
        "analytics",
        "financial",
        "scheduled",
    )
)
celery_app.conf.task_routes = {
    "app.worker.tasks.process_media_asset": {"queue": "media"},
    "app.worker.tasks.reconcile_financial_settlement": {"queue": "financial"},
    "app.worker.tasks.*": {"queue": "default"},
}
