import asyncio
import subprocess
from uuid import UUID

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


@celery_app.task(bind=True, autoretry_for=(ConnectionError,), retry_backoff=True, max_retries=3)
def process_media_asset(self, asset_id: str) -> dict[str, str]:
    """Idempotently create private derivatives for a finalized media upload."""
    from app.db.session import SessionLocal
    from app.media.processing import process_media_asset as process

    async def run() -> str:
        async with SessionLocal() as session:
            asset = await process(session, UUID(asset_id))
            await session.commit()
            return asset.status.value

    return {"asset_id": asset_id, "status": asyncio.run(run())}
