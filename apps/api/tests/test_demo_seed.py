from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models.content import ContentItem, ContentStatus, ContentType, MediaAsset
from app.models.creator import CreatorProfile, CreatorStatus
from app.models.featuring import FeatureBooking
from app.models.finance import LedgerTransaction, PaymentAttempt, Purchase
from app.models.groups import Group, GroupContract, GroupContractStatus
from app.models.identity import User
from app.models.marketplace import MarketplaceListing, MarketplaceListingStatus, MarketplaceOrder
from app.models.messaging import Conversation, Message
from app.models.notification import NotificationIntent
from app.models.referral import ReferralCommissionAllocation, SignupAttribution
from app.models.social import FeedPost, FeedPostStatus, Follow, PostComment, PostReaction
from app.models.streaming import LiveRoom, LiveRoomStatus
from app.models.subscription import Subscription
from app.seed import demo
from app.seed.build import seed_database
from app.seed.manifest import (
    CREATORS,
    PUBLIC_CREATORS,
    RESTRICTED_CREATOR,
    TARGET_CREATOR_COUNT,
    TARGET_GROUP_COUNT,
    TARGET_PUBLIC_CREATOR_COUNT,
    TARGET_PUBLISHED_CONTENT_COUNT,
    TARGET_PUBLISHED_LISTING_COUNT,
    TARGET_PUBLISHED_POST_COUNT,
    TARGET_PUBLISHED_VIDEO_COUNT,
    TARGET_USER_COUNT,
    USERS,
    expected_listing_titles,
    gallery_title,
    listing_count_for_creator,
    post_body,
    video_title,
)
from app.seed.media import ASSET_ROOT
from app.seed.validate import validate_database


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}

    def create_upload_url(self, key: str, content_type: str, expires_in: int) -> str:
        return f"memory://upload/{key}"

    def create_download_url(self, key: str, expires_in: int) -> str:
        return f"memory://download/{key}"

    def head(self, key: str) -> tuple[int, str]:
        body, content_type = self.objects[key]
        return len(body), content_type

    def get(self, key: str) -> bytes:
        return self.objects[key][0]

    def put(self, key: str, body: bytes, content_type: str) -> None:
        self.objects[key] = (body, content_type)

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)


@pytest.mark.asyncio
async def test_demo_guard_refuses_before_database_or_storage_access(monkeypatch):
    calls: list[str] = []

    monkeypatch.setattr(
        demo,
        "get_settings",
        lambda: SimpleNamespace(environment="production", demo_seed_enabled=True),
    )
    monkeypatch.setattr(demo, "storage_provider", lambda: calls.append("storage"))
    monkeypatch.setattr(demo, "SessionLocal", lambda: calls.append("database"))

    with pytest.raises(RuntimeError, match="requires FANBACKSTAGE_ENVIRONMENT=development"):
        await demo.seed()
    assert calls == []


def test_demo_manifest_has_stable_unique_count_invariants():
    assert len(USERS) == TARGET_USER_COUNT == 40
    assert len({item.email for item in USERS}) == len(USERS)
    assert len(CREATORS) == TARGET_CREATOR_COUNT == 13
    assert len(PUBLIC_CREATORS) == TARGET_PUBLIC_CREATOR_COUNT == 12
    assert len({item.slug for item in CREATORS}) == len(CREATORS)
    assert RESTRICTED_CREATOR.slug == "reya-restricted"
    assert sum(listing_count_for_creator(i) for i, _ in enumerate(PUBLIC_CREATORS)) == 18
    assert len(set(expected_listing_titles())) == TARGET_PUBLISHED_LISTING_COUNT
    assert len({gallery_title(item) for item in PUBLIC_CREATORS}) == 12
    assert len({video_title(item) for item in PUBLIC_CREATORS}) == 12
    assert len({post_body(item, i) for item in PUBLIC_CREATORS for i in range(4)}) == 48
    for creator in PUBLIC_CREATORS:
        assert (ASSET_ROOT / f"{creator.slug}.jpg").is_file()
        assert (ASSET_ROOT / f"{creator.slug}.mp4").is_file()
        assert creator.avatar_reference == f"/demo/creators/{creator.slug}/avatar.jpg"
        assert creator.cover_reference == f"/demo/creators/{creator.slug}/cover.jpg"


async def _snapshot(db_session) -> dict[str, int]:
    async def count(model, *criteria) -> int:
        return int(
            await db_session.scalar(select(func.count()).select_from(model).where(*criteria)) or 0
        )

    return {
        "users": await count(User),
        "creators": await count(CreatorProfile),
        "public_creators": await count(
            CreatorProfile,
            CreatorProfile.status == CreatorStatus.approved,
            CreatorProfile.is_public.is_(True),
        ),
        "suspended_creators": await count(
            CreatorProfile, CreatorProfile.status == CreatorStatus.suspended
        ),
        "media_assets": await count(MediaAsset),
        "published_content": await count(
            ContentItem, ContentItem.status == ContentStatus.published
        ),
        "published_videos": await count(
            ContentItem,
            ContentItem.status == ContentStatus.published,
            ContentItem.content_type == ContentType.video,
        ),
        "published_posts": await count(FeedPost, FeedPost.status == FeedPostStatus.published),
        "follows": await count(Follow),
        "reactions": await count(PostReaction),
        "comments": await count(PostComment),
        "groups": await count(Group),
        "active_group_contracts": await count(
            GroupContract, GroupContract.status == GroupContractStatus.active
        ),
        "published_listings": await count(
            MarketplaceListing,
            MarketplaceListing.status == MarketplaceListingStatus.published,
        ),
        "conversations": await count(Conversation),
        "messages": await count(Message),
        "subscriptions": await count(Subscription),
        "purchases": await count(Purchase),
        "marketplace_orders": await count(MarketplaceOrder),
        "payment_attempts": await count(PaymentAttempt),
        "ledger_transactions": await count(LedgerTransaction),
        "ended_live_rooms": await count(LiveRoom, LiveRoom.status == LiveRoomStatus.ended),
        "signup_attributions": await count(SignupAttribution),
        "referral_allocations": await count(ReferralCommissionAllocation),
        "feature_bookings": await count(FeatureBooking),
        "notification_intents": await count(NotificationIntent),
    }


@pytest.mark.asyncio
async def test_demo_seed_is_count_stable_when_run_twice(db_session):
    storage = MemoryStorage()
    first_stats = await seed_database(db_session, storage)
    await db_session.commit()
    first = await _snapshot(db_session)
    first_storage_count = len(storage.objects)

    second_stats = await seed_database(db_session, storage)
    await db_session.commit()
    second = await _snapshot(db_session)
    validation = await validate_database(db_session)

    assert asdict(first_stats) == asdict(second_stats)
    assert first == second
    assert len(storage.objects) == first_storage_count
    assert validation.ok, validation.failures
    assert second["users"] == TARGET_USER_COUNT
    assert second["creators"] == TARGET_CREATOR_COUNT
    assert second["public_creators"] == TARGET_PUBLIC_CREATOR_COUNT
    assert second["suspended_creators"] == 1
    assert second["published_content"] == TARGET_PUBLISHED_CONTENT_COUNT
    assert second["published_videos"] == TARGET_PUBLISHED_VIDEO_COUNT
    assert second["published_posts"] == TARGET_PUBLISHED_POST_COUNT
    assert second["published_listings"] == TARGET_PUBLISHED_LISTING_COUNT
    assert second["groups"] == TARGET_GROUP_COUNT
    assert second["active_group_contracts"] == TARGET_GROUP_COUNT
    assert second["follows"] >= 100
    assert second["reactions"] >= 150
    assert second["comments"] >= 80
    assert second["conversations"] >= 3
    assert second["messages"] >= 9
    assert second["subscriptions"] >= 5
    assert second["purchases"] >= 5
    assert second["marketplace_orders"] >= 3
    assert second["ended_live_rooms"] >= 1
    assert second["signup_attributions"] >= 1
    assert second["referral_allocations"] >= 1
    assert second["feature_bookings"] >= 1
    assert second["notification_intents"] >= 20
