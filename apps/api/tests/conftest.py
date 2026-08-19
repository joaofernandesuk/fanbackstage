import os

import pytest
from redis.asyncio import Redis
from sqlalchemy import text

if "FANBACKSTAGE_DATABASE_URL" not in os.environ:
    raise RuntimeError("Integration tests require FANBACKSTAGE_DATABASE_URL pointing to PostgreSQL")
os.environ.setdefault("FANBACKSTAGE_ENVIRONMENT", "test")

from app.core.config import get_settings
from app.db.session import SessionLocal


@pytest.fixture(autouse=True)
async def clean_database() -> None:
    async with SessionLocal() as session:
        await session.execute(
            text(
                "TRUNCATE audit_events, security_tokens, user_sessions, user_roles, users, roles CASCADE"
            )
        )
        await session.commit()
    redis = Redis.from_url(get_settings().redis_url)
    await redis.flushdb()
    await redis.aclose()
    yield


@pytest.fixture
async def db_session():
    async with SessionLocal() as session:
        yield session
