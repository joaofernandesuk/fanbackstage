import asyncio

import pytest
from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.discovery.service import current_config
from app.models.discovery import DiscoveryConfig


@pytest.mark.asyncio
async def test_initial_configuration_is_safe_under_concurrent_requests():
    ready = 0
    ready_lock = asyncio.Lock()
    release = asyncio.Event()

    async def load_config() -> int:
        nonlocal ready
        async with SessionLocal() as session:
            async with ready_lock:
                ready += 1
            await release.wait()
            config = await current_config(session)
            await session.commit()
            return config.version

    tasks = [asyncio.create_task(load_config()) for _ in range(8)]
    while True:
        async with ready_lock:
            if ready == len(tasks):
                break
        await asyncio.sleep(0)
    release.set()

    assert await asyncio.gather(*tasks) == [1] * len(tasks)
    async with SessionLocal() as session:
        assert await session.scalar(select(func.count()).select_from(DiscoveryConfig)) == 1
