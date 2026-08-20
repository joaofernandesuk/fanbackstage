from datetime import UTC, datetime

import pytest

from app.accounts import service as accounts
from app.content.access import can_access_content
from app.creators import service as creators
from app.models.content import (
    AccessPolicy,
    ContentEntitlement,
    ContentItem,
    ContentStatus,
    ContentType,
)
from app.models.creator import CreatorStatus


async def creator(db, email: str):
    user, _ = await accounts.register(db, email, "strong-password-123", None)
    profile = await creators.get_or_create_profile(db, user)
    await creators.update_profile(
        db, profile, {"username": email.split("@")[0], "display_name": "Creator"}, user.id
    )
    await creators.submit(db, profile, user.id)
    await creators.development_verify(db, profile, True, user.id)
    await creators.set_status(db, profile, CreatorStatus.approved, user.id)
    return user, profile


@pytest.mark.asyncio
async def test_content_access_is_free_only_by_default_and_entitlement_is_explicit(db_session):
    owner, profile = await creator(db_session, "owner@example.com")
    viewer, _ = await accounts.register(
        db_session, "viewer@example.com", "strong-password-123", None
    )
    content = ContentItem(
        owner_creator_id=profile.id,
        created_by_user_id=owner.id,
        content_type=ContentType.gallery,
        title="Private gallery",
        status=ContentStatus.published,
        access_policy=AccessPolicy.ppv,
    )
    db_session.add(content)
    await db_session.flush()
    assert not await can_access_content(db_session, content, None)
    assert not await can_access_content(db_session, content, viewer)
    assert await can_access_content(db_session, content, owner)
    db_session.add(
        ContentEntitlement(
            subject_user_id=viewer.id,
            content_id=content.id,
            source_type="admin_grant",
            valid_from=datetime.now(UTC),
        )
    )
    await db_session.flush()
    assert await can_access_content(db_session, content, viewer)
    content.access_policy = AccessPolicy.free
    assert await can_access_content(db_session, content, None)
