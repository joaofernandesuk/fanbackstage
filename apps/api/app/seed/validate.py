"""Read-only validation for the persistent local demonstration stack."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts import adult_access
from app.db.session import SessionLocal
from app.marketplace import service as marketplace
from app.models.content import (
    AccessPolicy,
    ContentItem,
    ContentStatus,
    ContentType,
    DerivativeType,
    Gallery,
    GalleryItem,
    MediaAsset,
    MediaAudience,
    MediaDerivative,
    MediaStatus,
    MediaType,
    VideoContent,
)
from app.models.creator import CreatorProfile, CreatorStatus
from app.models.featuring import FeatureBooking, FeatureBookingStatus
from app.models.finance import LedgerTransaction, Purchase, PurchaseStatus
from app.models.groups import Group, GroupContract, GroupContractStatus
from app.models.identity import User
from app.models.marketplace import (
    MarketplaceListing,
    MarketplaceListingMedia,
    MarketplaceListingStatus,
    MarketplaceOrder,
    MarketplaceOrderStatus,
)
from app.models.messaging import Conversation, Message
from app.models.notification import EmailSuppression, NotificationIntent, NotificationPreference
from app.models.referral import ReferralCommissionAllocation, ReferralProgram, SignupAttribution
from app.models.social import FeedPost, FeedPostStatus, Follow, PostComment, PostReaction
from app.models.story import Story, StoryStatus
from app.models.streaming import LiveRoom, LiveRoomStatus
from app.models.subscription import Subscription, SubscriptionStatus
from app.notifications.service import email_hash
from app.seed.demo import _assert_development
from app.seed.manifest import (
    CREATORS,
    GALLERY_SHOWCASES,
    GROUPS,
    PUBLIC_CREATORS,
    RESTRICTED_CREATOR,
    STORY_CREATORS,
    TARGET_ACTIVE_STORY_COUNT,
    TARGET_CREATOR_COUNT,
    TARGET_EXPIRED_STORY_COUNT,
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
    story_caption,
    video_title,
)
from app.seed.media import VIDEO_DURATION_SECONDS, VIDEO_PREVIEW_DURATION_SECONDS
from app.stories import service as stories


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
    story_creator_ids = [
        by_slug[creator.slug].id for creator in STORY_CREATORS if creator.slug in by_slug
    ]
    marketing_users = (
        await db.scalars(
            select(User).where(
                User.email.in_(
                    [
                        "marketing-in@demo.fanbackstage.local",
                        "marketing-out@demo.fanbackstage.local",
                    ]
                )
            )
        )
    ).all()
    marketing_users_by_email = {user.email: user for user in marketing_users}
    marketing_user_ids = [user.id for user in marketing_users]
    marketing_in = marketing_users_by_email.get("marketing-in@demo.fanbackstage.local")
    marketing_out = marketing_users_by_email.get("marketing-out@demo.fanbackstage.local")
    now = datetime.now(UTC)
    eligible_story_rows = (
        await db.execute(
            select(Story.id, Story.creator_id, Story.caption)
            .join(CreatorProfile, CreatorProfile.id == Story.creator_id)
            .join(MediaAsset, MediaAsset.id == Story.media_asset_id)
            .join(MediaDerivative, MediaDerivative.media_asset_id == MediaAsset.id)
            .where(
                Story.creator_id.in_(story_creator_ids),
                Story.status == StoryStatus.active,
                Story.expires_at > now,
                CreatorProfile.status == CreatorStatus.approved,
                CreatorProfile.is_public.is_(True),
                MediaAsset.status == MediaStatus.ready,
                MediaAsset.deleted_at.is_(None),
                MediaAsset.moderation_status.notin_(stories.UNSAFE_MODERATION_STATUSES),
                MediaDerivative.status == MediaStatus.ready,
                or_(
                    and_(
                        MediaAsset.media_type == MediaType.image,
                        MediaDerivative.derivative_type == DerivativeType.display,
                    ),
                    and_(
                        MediaAsset.media_type == MediaType.video,
                        MediaDerivative.derivative_type == DerivativeType.preview_clip,
                    ),
                ),
                ~stories.external_asset_reference(Story.media_asset_id),
            )
        )
    ).all()
    eligible_story_ids = {row.id for row in eligible_story_rows}
    eligible_story_pairs = {(row.creator_id, row.caption) for row in eligible_story_rows}
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
        "published_listing_media": int(
            await db.scalar(
                select(func.count(MarketplaceListingMedia.id))
                .join(
                    MarketplaceListing,
                    MarketplaceListing.id == MarketplaceListingMedia.listing_id,
                )
                .where(
                    MarketplaceListing.owner_creator_id.in_(public_ids),
                    MarketplaceListing.status == MarketplaceListingStatus.published,
                )
            )
            or 0
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
        "marketing_preference_rows": await _count(
            db,
            NotificationPreference,
            NotificationPreference.user_id.in_(marketing_user_ids),
            NotificationPreference.category == "marketing",
        ),
        "marketing_opted_in": (
            await _count(
                db,
                NotificationPreference,
                NotificationPreference.user_id == marketing_in.id,
                NotificationPreference.category == "marketing",
                NotificationPreference.email_enabled.is_(True),
                NotificationPreference.in_app_enabled.is_(True),
                NotificationPreference.consented_at.is_not(None),
                NotificationPreference.consent_source == "account_settings",
            )
            if marketing_in
            else 0
        ),
        "marketing_opted_out": (
            await _count(
                db,
                NotificationPreference,
                NotificationPreference.user_id == marketing_out.id,
                NotificationPreference.category == "marketing",
                NotificationPreference.email_enabled.is_(False),
                NotificationPreference.in_app_enabled.is_(True),
                NotificationPreference.consented_at.is_(None),
                NotificationPreference.consent_source.is_(None),
            )
            if marketing_out
            else 0
        ),
        "marketing_in_suppressions": (
            await _count(
                db,
                EmailSuppression,
                EmailSuppression.email_hash == email_hash(marketing_in.email),
            )
            if marketing_in
            else 0
        ),
        "active_stories": await _count(
            db,
            Story,
            Story.creator_id.in_(public_ids),
            Story.status == StoryStatus.active,
            Story.expires_at > now,
        ),
        "active_story_creators": int(
            await db.scalar(
                select(func.count(func.distinct(Story.creator_id))).where(
                    Story.creator_id.in_(story_creator_ids),
                    Story.status == StoryStatus.active,
                    Story.expires_at > now,
                )
            )
            or 0
        ),
        "eligible_active_stories": len(eligible_story_ids),
        "expired_stories": await _count(
            db,
            Story,
            Story.creator_id.in_(public_ids),
            Story.status == StoryStatus.expired,
        ),
        "overdue_active_stories": await _count(
            db,
            Story,
            Story.status == StoryStatus.active,
            Story.expires_at <= now,
        ),
        "story_media_owner_mismatches": int(
            await db.scalar(
                select(func.count())
                .select_from(Story)
                .join(MediaAsset, MediaAsset.id == Story.media_asset_id)
                .where(Story.creator_id != MediaAsset.owner_creator_id)
            )
            or 0
        ),
        "current_adult_attested_users": await _count(
            db,
            User,
            User.email.in_([seed.email for seed in USERS]),
            User.adult_attested_at.is_not(None),
            User.adult_attestation_version == adult_access.current_policy_version(),
        ),
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
    exact("current_adult_attested_users", TARGET_USER_COUNT)
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
    exact("published_listing_media", TARGET_PUBLISHED_LISTING_COUNT)
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
    exact("marketing_preference_rows", 2)
    exact("marketing_opted_in", 1)
    exact("marketing_opted_out", 1)
    exact("marketing_in_suppressions", 0)
    minimum("active_stories", TARGET_ACTIVE_STORY_COUNT)
    minimum("active_story_creators", len(STORY_CREATORS))
    minimum("eligible_active_stories", TARGET_ACTIVE_STORY_COUNT)
    minimum("expired_stories", TARGET_EXPIRED_STORY_COUNT)
    exact("overdue_active_stories", 0)
    exact("story_media_owner_mismatches", 0)

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
    for showcase in GALLERY_SHOWCASES:
        creator = by_slug.get(showcase.creator_slug)
        if not creator:
            failures.append(f"showcase gallery creator missing: {showcase.creator_slug}")
            continue
        row = await db.execute(
            select(ContentItem, Gallery, func.count(GalleryItem.id))
            .join(Gallery, Gallery.content_id == ContentItem.id)
            .outerjoin(GalleryItem, GalleryItem.gallery_id == Gallery.id)
            .where(
                ContentItem.owner_creator_id == creator.id,
                ContentItem.title == showcase.title,
                ContentItem.status == ContentStatus.published,
            )
            .group_by(ContentItem.id, Gallery.id)
        )
        gallery_row = row.first()
        if not gallery_row:
            failures.append(f"showcase gallery missing: {showcase.title}")
            continue
        content_item, gallery, item_count = gallery_row
        expected_policy = AccessPolicy(showcase.access_policy)
        if content_item.access_policy is not expected_policy:
            failures.append(f"showcase gallery policy mismatch: {showcase.title}")
        if int(item_count) != 4:
            failures.append(f"showcase gallery image count mismatch: {showcase.title}")
        if expected_policy is not AccessPolicy.free and gallery.preview_count < 1:
            failures.append(f"showcase gallery preview missing: {showcase.title}")
        if content_item.price_amount_minor != showcase.price_amount_minor:
            failures.append(f"showcase gallery price mismatch: {showcase.title}")
        gallery_assets = (
            await db.execute(
                select(GalleryItem.position, MediaAsset.audience)
                .join(MediaAsset, MediaAsset.id == GalleryItem.media_asset_id)
                .where(GalleryItem.gallery_id == gallery.id)
                .order_by(GalleryItem.position)
            )
        ).all()
        for position, audience in gallery_assets:
            expected_audience = (
                MediaAudience.adult_restricted
                if showcase.creator_slug == "zara-pulse" and position > 0
                else MediaAudience.safe_public
            )
            if audience is not expected_audience:
                failures.append(
                    f"showcase gallery audience mismatch: {showcase.title} item {position + 1}"
                )
    for creator_seed in PUBLIC_CREATORS:
        creator = by_slug.get(creator_seed.slug)
        if not creator:
            continue
        video_item = await db.scalar(
            select(ContentItem).where(
                ContentItem.owner_creator_id == creator.id,
                ContentItem.title == video_title(creator_seed),
            )
        )
        video = (
            await db.scalar(select(VideoContent).where(VideoContent.content_id == video_item.id))
            if video_item
            else None
        )
        asset = await db.get(MediaAsset, video.source_media_asset_id) if video else None
        if not asset:
            failures.append(f"demo video source missing: {creator_seed.slug}")
            continue
        expected_audience = (
            MediaAudience.adult_restricted
            if creator_seed.slug == "zara-pulse"
            else MediaAudience.safe_public
        )
        if asset.audience is not expected_audience:
            failures.append(f"demo video audience mismatch: {creator_seed.slug}")
        if (
            video.preview_start_seconds != 0
            or video.preview_duration_seconds != VIDEO_PREVIEW_DURATION_SECONDS
        ):
            failures.append(f"demo video preview configuration mismatch: {creator_seed.slug}")
        derivatives = (
            await db.scalars(
                select(MediaDerivative).where(
                    MediaDerivative.media_asset_id == asset.id,
                    MediaDerivative.derivative_type.in_(
                        [DerivativeType.playback, DerivativeType.preview_clip]
                    ),
                    MediaDerivative.status == MediaStatus.ready,
                )
            )
        ).all()
        by_type = {row.derivative_type: row for row in derivatives}
        playback = by_type.get(DerivativeType.playback)
        preview = by_type.get(DerivativeType.preview_clip)
        if (
            not playback
            or not preview
            or playback.duration_seconds is None
            or preview.duration_seconds is None
            or preview.duration_seconds >= playback.duration_seconds
            or asset.duration_seconds is None
            or preview.duration_seconds >= asset.duration_seconds
            or playback.duration_seconds != VIDEO_DURATION_SECONDS
            or asset.duration_seconds != VIDEO_DURATION_SECONDS
            or preview.duration_seconds != VIDEO_PREVIEW_DURATION_SECONDS
        ):
            failures.append(f"demo trailer is not shorter than playback: {creator_seed.slug}")
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
    published_listings = (
        await db.scalars(
            select(MarketplaceListing).where(
                MarketplaceListing.owner_creator_id.in_(public_ids),
                MarketplaceListing.status == MarketplaceListingStatus.published,
            )
        )
    ).all()
    for listing in published_listings:
        projected_media = await marketplace.public_listing_media(db, listing)
        if len(projected_media) != 1:
            failures.append(
                f"marketplace listing must have one dedicated safe display derivative: {listing.title}"
            )
    for creator in STORY_CREATORS:
        profile = by_slug.get(creator.slug)
        if not profile:
            failures.append(f"Story creator is missing: {creator.slug}")
            continue
        for position in range(3):
            expected_pair = (profile.id, story_caption(creator, position))
            if expected_pair not in eligible_story_pairs:
                failures.append(
                    f"eligible active Story is missing: {creator.slug} position {position + 1}"
                )
    restricted_story_count = (
        await _count(db, Story, Story.creator_id == restricted.id) if restricted else 0
    )
    if restricted_story_count:
        failures.append("reya-restricted must not have seeded Stories")
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
    print("\nDemo validation passed, including authoritative active and expired Stories.")


if __name__ == "__main__":
    asyncio.run(_main())
