"""Build the deterministic, fictional FanBackstage local demo dataset."""

from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.media.storage import storage_provider
from app.seed.build import seed_database


def _assert_development() -> None:
    """Refuse before a database session or storage client is constructed."""

    settings = get_settings()
    if settings.environment != "development" or not settings.demo_seed_enabled:
        raise RuntimeError(
            "Demo seeding requires FANBACKSTAGE_ENVIRONMENT=development and "
            "FANBACKSTAGE_DEMO_SEED_ENABLED=true"
        )


async def seed() -> None:
    _assert_development()
    provider = storage_provider()
    async with SessionLocal() as db:
        stats = await seed_database(db, provider)
        await db.commit()
    print(
        "Demo seed complete: "
        f"{stats.users} users, {stats.creators} creators, {stats.posts} published posts, "
        f"{stats.content_items} published content items, "
        f"{stats.listings} published marketplace listings, and "
        f"{stats.active_stories} active Stories."
    )


if __name__ == "__main__":
    asyncio.run(seed())
