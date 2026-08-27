import asyncio
import subprocess
from uuid import UUID

from app.core.config import get_settings
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


@celery_app.task(bind=True)
def deliver_notification(self, intent_id: str) -> dict[str, str]:
    """Deliver a persisted intent; retries cannot create another logical intent."""
    from app.db.session import SessionLocal
    from app.notifications.service import DeliveryStatus, deliver_intent

    async def run() -> str:
        async with SessionLocal() as session:
            status = await deliver_intent(session, UUID(intent_id))
            await session.commit()
            return status.value

    status = run_async(run())
    if (
        status == DeliveryStatus.failed_retryable.value
        and self.request.retries < get_settings().notification_max_attempts
    ):
        raise self.retry(
            countdown=get_settings().notification_retry_base_seconds * (2**self.request.retries)
        )
    return {"intent_id": intent_id, "status": status}


@celery_app.task
def reconcile_notification_delivery() -> dict[str, int]:
    from app.db.session import SessionLocal
    from app.notifications.service import reconcile_queued_intents

    async def run() -> int:
        async with SessionLocal() as session:
            reconciled = await reconcile_queued_intents(session)
            await session.commit()
            return reconciled

    return {"reconciled": run_async(run())}


@celery_app.task
def ffmpeg_version() -> dict[str, str]:
    """Worker-runtime capability check; no media input, output, or product state."""
    completed = subprocess.run(["ffmpeg", "-version"], check=True, capture_output=True, text=True)
    return {"version": completed.stdout.splitlines()[0]}


@celery_app.task(bind=True)
def process_media_asset(self, asset_id: str) -> dict[str, str]:
    """Idempotently create private derivatives for a finalized media upload."""
    from app.db.session import SessionLocal
    from app.media import processing

    async def run() -> str:
        async with SessionLocal() as session:
            try:
                asset = await processing.process_media_asset(session, UUID(asset_id))
                await session.commit()
                return asset.status.value
            except Exception:
                await session.commit()
                raise

    try:
        status = run_async(run())
    except processing.RetryableMediaProcessingError as exc:
        settings = get_settings()
        raise self.retry(
            exc=exc,
            countdown=min(60, 2**self.request.retries),
            max_retries=max(0, settings.media_processing_max_attempts - 1),
        ) from exc
    return {"asset_id": asset_id, "status": status}


@celery_app.task(bind=True)
def render_video_preview(
    self,
    content_id: str,
    expected_start_seconds: int | None = None,
    expected_duration_seconds: int | None = None,
) -> dict[str, str]:
    """Render a creator-selected video preview without exposing the source asset."""
    from app.db.session import SessionLocal
    from app.media import processing

    settings = get_settings()
    retry_transient_failure = self.request.retries + 1 < settings.media_processing_max_attempts

    async def run() -> bool:
        async with SessionLocal() as session:
            try:
                applied = await processing.render_video_preview(
                    session,
                    UUID(content_id),
                    expected_start_seconds=expected_start_seconds,
                    expected_duration_seconds=expected_duration_seconds,
                    retry_transient_failure=retry_transient_failure,
                )
                await session.commit()
                return applied
            except Exception:
                # Persist queued transient retries or the terminal failed state
                # without exposing or mutating the protected original asset.
                await session.commit()
                raise

    try:
        applied = run_async(run())
    except processing.RetryableMediaProcessingError as exc:
        raise self.retry(
            exc=exc,
            countdown=min(60, 2**self.request.retries),
            max_retries=max(0, settings.media_processing_max_attempts - 1),
        ) from exc
    return {"content_id": content_id, "status": "ready" if applied else "stale"}


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
def release_marketplace_earnings() -> dict[str, int]:
    """Durably release only delivery-confirmed, hold-expired marketplace orders."""
    from app.db.session import SessionLocal
    from app.marketplace.service import release_eligible_marketplace_earnings

    async def run() -> int:
        async with SessionLocal() as session:
            released = await release_eligible_marketplace_earnings(session)
            await session.commit()
            return released

    return {"released": run_async(run())}


@celery_app.task
def expire_marketplace_reservations() -> dict[str, int]:
    """Release expired marketplace stock reservations without process-local timers."""
    from app.db.session import SessionLocal
    from app.marketplace.service import expire_marketplace_reservations as expire

    async def run() -> int:
        async with SessionLocal() as session:
            released = await expire(session)
            await session.commit()
            return released

    return {"released": run_async(run())}


@celery_app.task
def reconcile_featuring_lifecycle() -> dict[str, int]:
    """Release expired holds and replay-safe activate/deactivate paid feature bookings."""
    from app.db.session import SessionLocal
    from app.featuring.service import (
        activate_due_bookings,
        deactivate_due_bookings,
        expire_reservations,
        revalidate_active_bookings,
    )

    async def run() -> dict[str, int]:
        async with SessionLocal() as session:
            expired = await expire_reservations(session)
            activated = await activate_due_bookings(session)
            revalidated = await revalidate_active_bookings(session)
            deactivated = await deactivate_due_bookings(session)
            await session.commit()
            return {
                "expired": expired,
                "activated": activated,
                "revalidated": revalidated,
                "deactivated": deactivated,
            }

    return run_async(run())


@celery_app.task
def expire_consent_releases() -> dict[str, int]:
    """Expire releases server-side; eligibility remains fail-closed even before this sweep."""
    from app.db.session import SessionLocal
    from app.trust_safety.service import expire_consent_releases as expire

    async def run() -> int:
        async with SessionLocal() as session:
            expired = await expire(session)
            await session.commit()
            return expired

    return {"expired": run_async(run())}


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


@celery_app.task
def publish_scheduled_posts() -> dict[str, int]:
    """Publish due feed posts durably and replay-safely."""
    from app.db.session import SessionLocal
    from app.social.service import publish_due_posts

    async def run() -> int:
        async with SessionLocal() as session:
            count = await publish_due_posts(session)
            await session.commit()
            return count

    return {"published": run_async(run())}


@celery_app.task
def expire_stories() -> dict[str, int]:
    """Expire due Stories durably; repeated or concurrent sweeps are harmless."""
    from app.db.session import SessionLocal
    from app.stories.service import expire_due_stories

    async def run() -> int:
        async with SessionLocal() as session:
            count = await expire_due_stories(session)
            await session.commit()
            return count

    return {"expired": run_async(run())}


@celery_app.task
def process_scheduled_mass_messages() -> dict[str, int]:
    """Durably fan out due messaging campaigns; duplicate recipients are prevented in PostgreSQL."""
    from app.db.session import SessionLocal
    from app.messaging.service import execute_due_campaigns

    async def run() -> int:
        async with SessionLocal() as session:
            count = await execute_due_campaigns(session)
            await session.commit()
            return count

    return {"delivered": run_async(run())}


@celery_app.task
def reconcile_private_session_grace() -> dict[str, int]:
    """Reconcile private reconnect deadlines and verified payment authorization."""
    from app.db.session import SessionLocal
    from app.streaming.service import (
        expire_reconnect_grace,
        reconcile_private_authorizations,
        reconcile_private_provider_presence,
    )

    async def run() -> dict[str, int]:
        async with SessionLocal() as session:
            ended = await expire_reconnect_grace(session)
            authorized = await reconcile_private_authorizations(session)
            presence_repaired = await reconcile_private_provider_presence(session)
            await session.commit()
            return {
                "ended": ended,
                "authorized": authorized,
                "presence_repaired": presence_repaired,
            }

    return run_async(run())
