"""Read-only validation for the persistent local demonstration stack."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import SessionLocal
from app.models.content import ContentItem, ContentStatus, ContentType, MediaAsset, MediaStatus
from app.models.creator import CreatorProfile, CreatorStatus
from app.models.featuring import FeatureBooking, FeatureBookingStatus
from app.models.finance import LedgerTransaction, Purchase, PurchaseStatus
from app.models.groups import Group, GroupContract, GroupContractStatus
from app.models.identity import User
from app.models.marketplace import (
    MarketplaceListing,
    MarketplaceListingStatus,
    MarketplaceOrder,
    MarketplaceOrderStatus,
)
from app.models.messaging import Conversation, Message
from app.models.notification import NotificationIntent
from app.models.referral import ReferralCommissionAllocation, ReferralProgram, SignupAttribution
from app.models.social import FeedPost, FeedPostStatus, Follow, PostComment, PostReaction
from app.models.streaming import LiveRoom, LiveRoomStatus
from app.models.subscription import Subscription, SubscriptionStatus
from app.seed.demo import _assert_development
from app.seed.manifest import (
    CREATORS,
    GROUPS,
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
    post_body,
    video_title,
)


@dataclass(frozen=True)
class ValidationReport:
    counts: dict[str, int]
    failures: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.failures


async def _count(db: AsyncSession, model, *criteria) -> int:
    return int(await db.scalar(select(func.count()).select_from(model).where(*criteria)) or 0)


async def validate_database(db: AsyncSession) -> ValidationReport:
    """Inspect canonical state without creating, updating, or deleting any row."""

    profiles = (
        await db.scalars(
            select(CreatorProfile).where(
                CreatorProfile.username.in_([creator.slug for creator in CREATORS])
            )
        )
    ).all()
    by_slug = {profile.username: profile for profile in profiles}
    public_ids = [
        by_slug[creator.slug].id for creator in PUBLIC_CREATORS if creator.slug in by_slug
    ]
    counts = {
        "users": await _count(db, User),
        "manifest_users": await _count(db, User, User.email.in_([item.email for item in USERS])),
        "creators": await _count(db, CreatorProfile),
        "manifest_creators": len(profiles),
        "public_creators": await _count(
            db,
            CreatorProfile,
            CreatorProfile.status == CreatorStatus.approved,
            CreatorProfile.is_public.is_(True),
        ),
        "ready_media": await _count(db, MediaAsset, MediaAsset.status == MediaStatus.ready),
        "published_content": await _count(
            db,
            ContentItem,
            ContentItem.owner_creator_id.in_(public_ids),
            ContentItem.status == ContentStatus.published,
        ),
        "published_videos": await _count(
            db,
            ContentItem,
            ContentItem.owner_creator_id.in_(public_ids),
            ContentItem.status == ContentStatus.published,
            ContentItem.content_type == ContentType.video,
        ),
        "published_posts": await _count(
            db,
            FeedPost,
            FeedPost.creator_id.in_(public_ids),
            FeedPost.status == FeedPostStatus.published,
        ),
        "follows": await _count(db, Follow),
        "reactions": await _count(db, PostReaction),
        "comments": await _count(db, PostComment),
        "groups": await _count(db, Group),
        "active_group_contracts": await _count(
            db, GroupContract, GroupContract.status == GroupContractStatus.active
        ),
        "published_listings": await _count(
            db,
            MarketplaceListing,
            MarketplaceListing.owner_creator_id.in_(public_ids),
            MarketplaceListing.status == MarketplaceListingStatus.published,
        ),
        "conversations": await _count(db, Conversation),
        "messages": await _count(db, Message),
        "subscriptions": await _count(db, Subscription),
        "active_subscriptions": await _count(
            db, Subscription, Subscription.status == SubscriptionStatus.active
        ),
        "ppv_purchases": await _count(db, Purchase),
        "paid_ppv_purchases": await _count(db, Purchase, Purchase.status == PurchaseStatus.paid),
        "marketplace_orders": await _count(db, MarketplaceOrder),
        "settled_marketplace_orders": await _count(
            db,
            MarketplaceOrder,
            MarketplaceOrder.status.in_(
                [
                    MarketplaceOrderStatus.paid,
                    MarketplaceOrderStatus.processing,
                    MarketplaceOrderStatus.shipped,
                    MarketplaceOrderStatus.delivered,
                ]
            ),
        ),
        "ledger_transactions": await _count(db, LedgerTransaction),
        "ended_live_rooms": await _count(db, LiveRoom, LiveRoom.status == LiveRoomStatus.ended),
        "active_live_rooms": await _count(
            db,
            LiveRoom,
            LiveRoom.status.in_(
                [LiveRoomStatus.starting, LiveRoomStatus.live, LiveRoomStatus.ending]
            ),
        ),
        "referral_programs": await _count(db, ReferralProgram),
        "signup_attributions": await _count(db, SignupAttribution),
        "referral_allocations": await _count(db, ReferralCommissionAllocation),
        "feature_bookings": await _count(db, FeatureBooking),
        "active_feature_bookings": await _count(
            db, FeatureBooking, FeatureBooking.status == FeatureBookingStatus.active
        ),
        "notification_intents": await _count(db, NotificationIntent),
    }
    failures: list[str] = []

    def exact(key: str, expected: int) -> None:
        if counts[key] != expected:
            failures.append(f"{key}: expected exactly {expected}, found {counts[key]}")

    def minimum(key: str, expected: int) -> None:
        if counts[key] < expected:
            failures.append(f"{key}: expected at least {expected}, found {counts[key]}")

    exact("users", TARGET_USER_COUNT)
    exact("manifest_users", TARGET_USER_COUNT)
    exact("creators", TARGET_CREATOR_COUNT)
    exact("manifest_creators", TARGET_CREATOR_COUNT)
    exact("public_creators", TARGET_PUBLIC_CREATOR_COUNT)
    exact("groups", TARGET_GROUP_COUNT)
    exact("active_group_contracts", TARGET_GROUP_COUNT)
    minimum("ready_media", 24)
    minimum("published_content", TARGET_PUBLISHED_CONTENT_COUNT)
    minimum("published_videos", TARGET_PUBLISHED_VIDEO_COUNT)
    minimum("published_posts", TARGET_PUBLISHED_POST_COUNT)
    minimum("published_listings", TARGET_PUBLISHED_LISTING_COUNT)
    minimum("follows", 100)
    minimum("reactions", 150)
    minimum("comments", 80)
    minimum("conversations", 3)
    minimum("messages", 9)
    minimum("subscriptions", 5)
    minimum("active_subscriptions", 5)
    minimum("ppv_purchases", 5)
    minimum("paid_ppv_purchases", 5)
    minimum("marketplace_orders", 3)
    minimum("settled_marketplace_orders", 3)
    minimum("ledger_transactions", 15)
    minimum("ended_live_rooms", 1)
    exact("active_live_rooms", 0)
    minimum("referral_programs", 1)
    minimum("signup_attributions", 1)
    minimum("referral_allocations", 1)
    minimum("feature_bookings", 1)
    minimum("active_feature_bookings", 1)
    minimum("notification_intents", 20)

    for creator in CREATORS:
        profile = by_slug.get(creator.slug)
        if not profile:
            failures.append(f"creator missing: {creator.slug}")
            continue
        if profile.avatar_reference != creator.avatar_reference:
            failures.append(f"avatar reference mismatch: {creator.slug}")
        if profile.cover_reference != creator.cover_reference:
            failures.append(f"cover reference mismatch: {creator.slug}")
    restricted = by_slug.get(RESTRICTED_CREATOR.slug)
    if not restricted or restricted.status is not CreatorStatus.suspended or restricted.is_public:
        failures.append("reya-restricted must remain suspended and non-public")

    expected_content_titles = {
        title
        for creator in PUBLIC_CREATORS
        for title in (gallery_title(creator), video_title(creator))
    }
    actual_content_titles = set(
        (
            await db.scalars(
                select(ContentItem.title).where(
                    ContentItem.owner_creator_id.in_(public_ids),
                    ContentItem.status == ContentStatus.published,
                )
            )
        ).all()
    )
    if not expected_content_titles <= actual_content_titles:
        failures.append("one or more manifest content titles are missing")
    expected_post_bodies = {
        post_body(creator, position) for creator in PUBLIC_CREATORS for position in range(4)
    }
    actual_post_bodies = set(
        (
            await db.scalars(
                select(FeedPost.body).where(
                    FeedPost.creator_id.in_(public_ids),
                    FeedPost.status == FeedPostStatus.published,
                )
            )
        ).all()
    )
    if not expected_post_bodies <= actual_post_bodies:
        failures.append("one or more manifest feed posts are missing")
    actual_listing_titles = set(
        (
            await db.scalars(
                select(MarketplaceListing.title).where(
                    MarketplaceListing.owner_creator_id.in_(public_ids),
                    MarketplaceListing.status == MarketplaceListingStatus.published,
                )
            )
        ).all()
    )
    if not set(expected_listing_titles()) <= actual_listing_titles:
        failures.append("one or more manifest marketplace listings are missing")
    if {group.slug for group in (await db.scalars(select(Group))).all()} != {
        group[0] for group in GROUPS
    }:
        failures.append("group slug manifest does not match the database")
    return ValidationReport(counts, tuple(failures))


async def validate() -> ValidationReport:
    _assert_development()
    async with SessionLocal() as db:
        return await validate_database(db)


async def _main() -> None:
    report = await validate()
    for key, value in sorted(report.counts.items()):
        print(f"{key:24} {value}")
    if report.failures:
        print("\nDemo validation failed:")
        for failure in report.failures:
            print(f"- {failure}")
        raise SystemExit(1)
    print(
        "\nDemo validation passed. Stories are intentionally omitted: no backend Stories domain exists."
    )


if __name__ == "__main__":
    asyncio.run(_main())
