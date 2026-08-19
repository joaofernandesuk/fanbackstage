import subprocess

from app.worker.celery_app import celery_app


@celery_app.task(bind=True, autoretry_for=(ConnectionError,), retry_backoff=True, max_retries=3)
def health_ping(self) -> dict[str, str]:
    """Harmless worker readiness task; it creates no durable product state."""
    return {"status": "ok", "queue": self.request.delivery_info.get("routing_key", "default")}


@celery_app.task
def ffmpeg_version() -> dict[str, str]:
    """Worker-runtime capability check; no media input, output, or product state."""
    completed = subprocess.run(["ffmpeg", "-version"], check=True, capture_output=True, text=True)
    return {"version": completed.stdout.splitlines()[0]}
