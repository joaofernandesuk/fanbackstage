import asyncio
import subprocess
from uuid import UUID

from app.worker.celery_app import celery_app

_event_loop: asyncio.AbstractEventLoop | None = None


def run_async(coroutine):
    """Run async database work on one loop per Celery worker process.

    SQLAlchemy's asyncpg pool is loop-affine.  Creating a new loop with
    ``asyncio.run`` for every task eventually attaches pooled connections to
    the wrong loop, leaving later media jobs queued.
    """
    global _event_loop
    if _event_loop is None or _event_loop.is_closed():
        _event_loop = asyncio.new_event_loop()
    return _event_loop.run_until_complete(coroutine)


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
            try:
                asset = await process(session, UUID(asset_id))
                await session.commit()
                return asset.status.value
            except Exception:
                await session.commit()
                raise

    return {"asset_id": asset_id, "status": run_async(run())}


@celery_app.task(bind=True, autoretry_for=(ConnectionError,), retry_backoff=True, max_retries=3)
def render_video_preview(self, content_id: str) -> dict[str, str]:
    """Render a creator-selected video preview without exposing the source asset."""
    from app.db.session import SessionLocal
    from app.media.processing import render_video_preview as render

    async def run() -> None:
        async with SessionLocal() as session:
            await render(session, UUID(content_id))
            await session.commit()

    run_async(run())
    return {"content_id": content_id, "status": "ready"}


@celery_app.task
def reconcile_financial_settlement() -> dict[str, int]:
    """Replay-safe settlement recovery for payment confirmations."""
    from app.db.session import SessionLocal
    from app.finance.service import reconcile_succeeded_payments

    async def run() -> int:
        async with SessionLocal() as session:
            reconciled = await reconcile_succeeded_payments(session)
            await session.commit()
            return reconciled

    return {"reconciled": run_async(run())}


@celery_app.task
def process_subscription_renewals() -> dict[str, int]:
    """Create one replay-safe renewal attempt per due subscription period."""
    from app.db.session import SessionLocal
    from app.subscriptions.service import renew_due_subscriptions

    async def run() -> int:
        async with SessionLocal() as session:
            value = await renew_due_subscriptions(session)
            await session.commit()
            return value

    return {"created": run_async(run())}


@celery_app.task
def finalize_subscription_expirations() -> dict[str, int]:
    """End expired or exhausted-grace subscriptions without mutating ledger history."""
    from app.db.session import SessionLocal
    from app.subscriptions.service import finalize_expired_subscriptions

    async def run() -> int:
        async with SessionLocal() as session:
            value = await finalize_expired_subscriptions(session)
            await session.commit()
            return value

    return {"expired": run_async(run())}


@celery_app.task
def retry_subscription_renewals() -> dict[str, int]:
    """Create bounded, durable retries for failed renewal charges."""
    from app.db.session import SessionLocal
    from app.subscriptions.service import retry_failed_subscription_renewals

    async def run() -> int:
        async with SessionLocal() as session:
            value = await retry_failed_subscription_renewals(session)
            await session.commit()
            return value

    return {"created": run_async(run())}
