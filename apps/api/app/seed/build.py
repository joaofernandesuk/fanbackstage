"""Replay-safe construction of the local demonstration dataset.

Domain services own every state transition and every value-moving operation.
The few direct rows below are seed-only catalogue/configuration adapters for
concepts that intentionally have no write service (profile catalogue links,
pre-rendered media derivatives, reactions, and comments).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.accounts import service as accounts
from app.content import service as content_service
from app.creators import service as creators
from app.featuring import service as featuring
from app.finance import service as finance
from app.groups import service as groups
from app.marketplace import service as marketplace
from app.messaging import service as messaging
from app.models.content import (
    AccessPolicy,
    ContentItem,
    ContentStatus,
    Gallery,
    MediaAsset,
)
from app.models.creator import (
    CreatorCategory,
    CreatorLanguage,
    CreatorProfile,
    CreatorSocialLink,
    CreatorStatus,
    CreatorVerification,
    VerificationStatus,
)
from app.models.featuring import (
    FeatureBooking,
    FeatureBookingStatus,
    FeaturePrice,
    FeatureSlot,
    FeatureSurfaceKind,
    FeatureTargetType,
)
from app.models.finance import PaymentAttempt, PaymentStatus
from app.models.groups import (
    Group,
    GroupCreatorMembership,
    GroupMembershipStatus,
    GroupPermission,
)
from app.models.identity import User
from app.models.marketplace import (
    MarketplaceEarningsHoldPolicy,
    MarketplaceListing,
    MarketplaceListingStatus,
    MarketplaceOrderStatus,
    MarketplaceSellerTier,
    MarketplaceShippingAllowance,
    ShippingAllowanceScope,
)
from app.models.messaging import Conversation, Message
from app.models.referral import (
    ReferralActorType,
    ReferralCommissionPolicy,
    ReferralLink,
    ReferralPolicyStatus,
    ReferralProgram,
    ReferralProgramType,
    SignupAttribution,
)
from app.models.social import (
    FeedPost,
    FeedPostStatus,
    PostComment,
    PostReaction,
    ReactionType,
)
from app.models.story import Story, StoryStatus
from app.models.streaming import LiveAccessMode, LiveRoom
from app.models.subscription import SubscriptionPeriod
from app.referrals import service as referrals
from app.seed.manifest import (
    CORE_USERS,
    CREATORS,
    FAN_USERS,
    GROUPS,
    PASSWORD,
    PUBLIC_CREATORS,
    RESTRICTED_CREATOR,
    STORY_CREATORS,
    USERS,
    CreatorSeed,
    gallery_title,
    listing_count_for_creator,
    listing_title,
    post_body,
    story_caption,
    story_cohort_idempotency_key,
    video_title,
)
from app.seed.media import (
    ASSET_ROOT,
    ensure_image_asset,
    ensure_video_asset,
    restore_video_preview_ready,
)
from app.social import service as social
from app.stories import service as stories
from app.streaming import service as streaming
from app.subscriptions import service as subscriptions


@dataclass(frozen=True)
class SeedStats:
    users: int
    creators: int
    posts: int
    content_items: int
    listings: int
    active_stories: int


@dataclass(frozen=True)
class CreatorContent:
    gallery: ContentItem
    video: ContentItem
    image_asset: MediaAsset
    video_asset: MediaAsset


def _email(local_part: str) -> str:
    return f"{local_part}@demo.fanbackstage.local"


async def _ensure_user(db: AsyncSession, email: str, role_names: tuple[str, ...]) -> User:
    user = await db.scalar(select(User).where(User.email == email))
    if not user:
        user, _ = await accounts.register(db, email, PASSWORD, None)
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
    for role_name in role_names:
        if role_name not in {role.name for role in user.roles}:
            await accounts.assign_role(db, user, role_name, user.id, None)
    return user


async def _ensure_creator_catalogue(
    db: AsyncSession, profile: CreatorProfile, seed: CreatorSeed
) -> None:
    category = await db.scalar(select(CreatorCategory).where(CreatorCategory.slug == seed.category))
    if not category:
        category = CreatorCategory(
            slug=seed.category,
            label=seed.category.replace("-", " ").title(),
            position=len(profile.categories),
        )
        db.add(category)
        await db.flush()
    if category not in profile.categories:
        profile.categories.append(category)
    language = await db.scalar(
        select(CreatorLanguage).where(CreatorLanguage.code == seed.language_code)
    )
    if not language:
        language = CreatorLanguage(code=seed.language_code, label=seed.language_label)
        db.add(language)
        await db.flush()
    if language not in profile.languages:
        profile.languages.append(language)
    link_url = f"https://demo.fanbackstage.local/{seed.slug}"
    if not any(link.url == link_url for link in profile.links):
        profile.links.append(CreatorSocialLink(label="Demo portfolio", url=link_url, position=0))


async def _ensure_creator(
    db: AsyncSession, admin: User, user: User, seed: CreatorSeed
) -> CreatorProfile:
    profile = await creators.get_or_create_profile(db, user)
    await db.refresh(profile, ["categories", "languages", "links"])
    await creators.update_profile(
        db,
        profile,
        {
            "username": seed.slug,
            "display_name": seed.display_name,
            "bio": seed.bio,
            "country_code": "PT",
            "region": seed.city,
            "city": seed.city,
            "show_location": True,
            "timezone": "Europe/Lisbon",
        },
        user.id,
    )
    profile.avatar_reference = seed.avatar_reference
    profile.cover_reference = seed.cover_reference
    await _ensure_creator_catalogue(db, profile, seed)
    if profile.status is CreatorStatus.draft:
        await creators.submit(db, profile, user.id)
    if profile.status is CreatorStatus.pending_verification:
        verified = await db.scalar(
            select(CreatorVerification).where(
                CreatorVerification.creator_profile_id == profile.id,
                CreatorVerification.status == VerificationStatus.verified,
                CreatorVerification.adult_verified.is_(True),
            )
        )
        if verified:
            await creators.set_status(db, profile, CreatorStatus.pending_review, admin.id)
        else:
            await creators.development_verify(db, profile, True, admin.id)
    if profile.status is CreatorStatus.pending_review:
        await creators.set_status(db, profile, CreatorStatus.approved, admin.id)
    if seed == RESTRICTED_CREATOR:
        if profile.status is CreatorStatus.approved:
            await creators.set_status(
                db,
                profile,
                CreatorStatus.suspended,
                admin.id,
                "Fictional demo profile retained for a safety review workflow",
            )
        profile.is_public = False
    elif profile.status is CreatorStatus.approved:
        await creators.update_profile(db, profile, {"is_public": True}, user.id)
    else:
        raise RuntimeError(
            f"Demo creator {seed.slug} is {profile.status.value}; "
            "the seed will not override an existing moderation decision"
        )
    return profile


async def _ensure_referral(db: AsyncSession, users: dict[str, User]) -> None:
    owner = users[_email("marketing-in")]
    attributed_user = users[_email("ppvbuyer")]
    program = await db.scalar(
        select(ReferralProgram).where(
            ReferralProgram.actor_type == ReferralActorType.user,
            ReferralProgram.program_type == ReferralProgramType.user_user_referral,
            ReferralProgram.owner_user_id == owner.id,
        )
    )
    if not program:
        program = await referrals.create_program(
            db,
            actor_type=ReferralActorType.user,
            program_type=ReferralProgramType.user_user_referral,
            owner_user_id=owner.id,
            terms_reference="demo://local-referral-terms-v1",
        )
    policy = await db.scalar(
        select(ReferralCommissionPolicy)
        .where(
            ReferralCommissionPolicy.program_id == program.id,
            ReferralCommissionPolicy.status == ReferralPolicyStatus.active,
        )
        .order_by(ReferralCommissionPolicy.version.desc())
    )
    if not policy:
        policy = await referrals.create_policy(
            db,
            program,
            basis_points=1_000,
            eligible_revenue_types=["marketplace", "ppv", "subscription"],
        )
    link = await db.scalar(select(ReferralLink).where(ReferralLink.code == "DEMO-FANBACKSTAGE"))
    if not link:
        link = await referrals.create_link(
            db,
            program,
            policy,
            code="DEMO-FANBACKSTAGE",
            destination_path="/discover",
            source="local-demo",
        )
    attribution = await db.scalar(
        select(SignupAttribution).where(SignupAttribution.user_id == attributed_user.id)
    )
    if not attribution:
        _, token = await referrals.resolve_click(
            db,
            link.code,
            "fanbackstage-deterministic-demo-referral-session",
            source="local-demo",
            utm={"source": "demo", "campaign": "local-rebuild"},
        )
        await referrals.snapshot_signup_attribution(db, attributed_user, token)


async def _ensure_groups(
    db: AsyncSession,
    users: dict[str, User],
    profiles: dict[str, CreatorProfile],
) -> None:
    manager = users[_email("manager")]
    permissions = [
        GroupPermission.manage_content,
        GroupPermission.publish_posts,
        GroupPermission.view_analytics,
        GroupPermission.view_earnings,
        GroupPermission.manage_marketplace,
        GroupPermission.manage_marketplace_orders,
        GroupPermission.manage_featuring,
    ]
    for slug, name, creator_slug, creator_bps in GROUPS:
        group = await db.scalar(select(Group).where(Group.slug == slug))
        if not group:
            group = await groups.create_group(
                db,
                manager,
                name,
                slug,
                creator_bps,
                "Fictional development-only creator management agency.",
            )
        profile = profiles[creator_slug]
        membership = await db.scalar(
            select(GroupCreatorMembership).where(
                GroupCreatorMembership.group_id == group.id,
                GroupCreatorMembership.creator_id == profile.id,
                GroupCreatorMembership.status.in_(
                    [
                        GroupMembershipStatus.invited,
                        GroupMembershipStatus.pending_acceptance,
                        GroupMembershipStatus.active,
                        GroupMembershipStatus.leaving,
                    ]
                ),
            )
        )
        if not membership:
            membership = await groups.invite_creator(
                db,
                group.id,
                manager,
                profile.id,
                creator_bps,
                permissions,
            )
        if membership.status is GroupMembershipStatus.invited:
            await groups.accept_invitation(
                db, membership.id, users[f"{creator_slug}@demo.fanbackstage.local"]
            )


def _gallery_policy(position: int) -> AccessPolicy:
    return (
        AccessPolicy.free,
        AccessPolicy.followers,
        AccessPolicy.subscription,
    )[position % 3]


def _video_policy(position: int, creator: CreatorSeed) -> AccessPolicy:
    if position % 2 == 0 or creator.slug == "aria-group":
        return AccessPolicy.ppv
    return AccessPolicy.subscription if position % 3 == 1 else AccessPolicy.free


async def _content_by_title(db: AsyncSession, creator_id, title: str) -> ContentItem | None:
    return await db.scalar(
        select(ContentItem)
        .options(
            selectinload(ContentItem.gallery).selectinload(Gallery.items),
            selectinload(ContentItem.video),
        )
        .where(ContentItem.owner_creator_id == creator_id, ContentItem.title == title)
    )


async def _ensure_creator_content(
    db: AsyncSession,
    admin: User,
    creator_user: User,
    profile: CreatorProfile,
    seed: CreatorSeed,
    position: int,
    provider,
    asset_root: Path,
) -> CreatorContent:
    image_asset = await ensure_image_asset(
        db, creator_user, profile, seed.slug, provider, asset_root
    )
    video_asset = await ensure_video_asset(
        db, creator_user, profile, seed.slug, provider, asset_root
    )
    feed_settings = await social.settings_for_creator(db, profile.id)
    feed_settings.auto_post_galleries = False
    feed_settings.auto_post_videos = False

    gallery = await _content_by_title(db, profile.id, gallery_title(seed))
    gallery_policy = _gallery_policy(position)
    if not gallery:
        gallery = await content_service.create_gallery(
            db,
            creator_user,
            gallery_title(seed),
            "A fictional, harmless collection produced for local product testing.",
            gallery_policy,
        )
    assert gallery.gallery
    await db.refresh(gallery.gallery, ["items"])
    if not gallery.gallery.items:
        await content_service.add_gallery_item(
            db,
            creator_user,
            gallery.id,
            image_asset.id,
            preview=gallery_policy is not AccessPolicy.free,
        )
        if gallery_policy is not AccessPolicy.free:
            await content_service.configure_gallery_preview(
                db, creator_user, gallery.id, 1, {image_asset.id}
            )
    if gallery.status is ContentStatus.processing:
        await content_service.submit_for_review(db, creator_user, gallery.id)
    if gallery.status is ContentStatus.pending_review:
        await content_service.approve(db, gallery, admin)

    video = await _content_by_title(db, profile.id, video_title(seed))
    video_policy = _video_policy(position, seed)
    if not video:
        video = await content_service.create_video(
            db,
            creator_user,
            video_title(seed),
            "A three-second fictional studio reel for exercising protected video delivery.",
            video_asset.id,
            video_policy,
            preview_start_seconds=0,
            preview_duration_seconds=3,
            price_amount_minor=599 + position * 25 if video_policy is AccessPolicy.ppv else None,
            price_currency="EUR" if video_policy is AccessPolicy.ppv else None,
        )
        await restore_video_preview_ready(db, video_asset.id)
    if video.status is ContentStatus.processing:
        await restore_video_preview_ready(db, video_asset.id)
        await content_service.submit_for_review(db, creator_user, video.id)
    if video.status is ContentStatus.pending_review:
        await content_service.approve(db, video, admin)
    if gallery.status is not ContentStatus.published or video.status is not ContentStatus.published:
        raise RuntimeError(f"Demo content did not publish for {seed.slug}")
    return CreatorContent(gallery, video, image_asset, video_asset)


async def _ensure_posts(
    db: AsyncSession,
    creator_user: User,
    profile: CreatorProfile,
    seed: CreatorSeed,
    bundle: CreatorContent,
) -> list[FeedPost]:
    values = (
        {
            "post_type": "image",
            "body": post_body(seed, 0),
            "media_asset_ids": [bundle.image_asset.id],
        },
        {"post_type": "text", "body": post_body(seed, 1)},
        {
            "post_type": "gallery_reference",
            "body": post_body(seed, 2),
            "content_id": bundle.gallery.id,
        },
        {
            "post_type": "video_reference",
            "body": post_body(seed, 3),
            "content_id": bundle.video.id,
        },
    )
    rows: list[FeedPost] = []
    for item in values:
        post = await db.scalar(
            select(FeedPost).where(
                FeedPost.creator_id == profile.id,
                FeedPost.body == item["body"],
            )
        )
        if not post:
            post = await social.create_post(
                db,
                creator_user,
                {**item, "access_policy": AccessPolicy.free},
            )
        if post.status in {FeedPostStatus.draft, FeedPostStatus.scheduled}:
            await social.publish(db, creator_user, post.id)
        rows.append(post)
    return rows


async def _ensure_stories(
    db: AsyncSession,
    users: dict[str, User],
    profiles: dict[str, CreatorProfile],
    provider,
    asset_root: Path,
) -> None:
    """Keep a fresh 24-hour Story set while retaining expired demo history."""

    reference = datetime.now(UTC)
    await stories.expire_due_stories(db, now=reference)
    for creator_position, seed in enumerate(STORY_CREATORS):
        creator = profiles[seed.slug]
        creator_user = users[seed.email]
        story_image = await ensure_image_asset(
            db,
            creator_user,
            creator,
            seed.slug,
            provider,
            asset_root,
            variant="story",
        )
        story_video = await ensure_video_asset(
            db,
            creator_user,
            creator,
            seed.slug,
            provider,
            asset_root,
            variant="story",
        )
        for position in range(3):
            caption = story_caption(seed, position)
            existing = await db.scalar(
                select(Story.id).where(
                    Story.creator_id == creator.id,
                    Story.caption == caption,
                    Story.status == StoryStatus.active,
                    Story.expires_at > reference,
                )
            )
            if existing:
                continue
            if position < 2 or creator_position % 3 == 2:
                policy = AccessPolicy.free
            elif creator_position % 3 == 0:
                policy = AccessPolicy.followers
            else:
                policy = AccessPolicy.subscription
            await stories.create_story(
                db,
                creator_user,
                (story_image.id if position % 2 == 0 else story_video.id),
                caption,
                f"{seed.display_name} demo Story {position + 1}",
                policy,
                story_cohort_idempotency_key(seed, position, reference),
                now=reference - timedelta(minutes=(creator_position * 10) + position + 1),
            )

        if creator_position >= 4:
            continue
        caption = story_caption(seed, creator_position, historical=True)
        historical = await db.scalar(
            select(Story.id).where(
                Story.creator_id == creator.id,
                Story.caption == caption,
            )
        )
        if not historical:
            await stories.create_story(
                db,
                creator_user,
                story_image.id,
                caption,
                f"Expired {seed.display_name} demo Story",
                AccessPolicy.free,
                f"demo-story-{seed.slug}-historical-{creator_position + 1}",
                now=reference - timedelta(days=2, minutes=creator_position),
            )
    await stories.expire_due_stories(db, now=reference)


async def _ensure_social_graph(
    db: AsyncSession,
    users: dict[str, User],
    profiles: dict[str, CreatorProfile],
    posts: dict[str, list[FeedPost]],
) -> None:
    fan_seeds = CORE_USERS + FAN_USERS
    public_profiles = [profiles[seed.slug] for seed in PUBLIC_CREATORS]
    for position, fan_seed in enumerate(fan_seeds):
        fan = users[fan_seed.email]
        for offset in (0, 3, 6, 9):
            await social.follow(
                db, fan, public_profiles[(position + offset) % len(public_profiles)].id
            )
    reaction_users = [users[item.email] for item in fan_seeds[:8]]
    comment_users = [users[item.email] for item in fan_seeds[8:12]]
    reaction_types = tuple(ReactionType)
    for creator_position, creator_seed in enumerate(PUBLIC_CREATORS):
        for post_position, post in enumerate(posts[creator_seed.slug][:2]):
            for user_position, user in enumerate(reaction_users):
                if not await social.can_access_post(db, post, user):
                    raise RuntimeError("Demo social engagement targets an inaccessible post")
                reaction = await db.scalar(
                    select(PostReaction).where(
                        PostReaction.post_id == post.id,
                        PostReaction.user_id == user.id,
                    )
                )
                if not reaction:
                    db.add(
                        PostReaction(
                            post_id=post.id,
                            user_id=user.id,
                            reaction_type=reaction_types[
                                (creator_position + post_position + user_position)
                                % len(reaction_types)
                            ],
                        )
                    )
            for user_position, user in enumerate(comment_users):
                body = (
                    "The lighting and color story are lovely."
                    if user_position % 2 == 0
                    else "This is such a welcoming behind-the-scenes update!"
                )
                body = f"{body} [{creator_seed.slug}:{post_position}:{user_position}]"
                comment = await db.scalar(
                    select(PostComment).where(
                        PostComment.post_id == post.id,
                        PostComment.user_id == user.id,
                        PostComment.body == body,
                    )
                )
                if not comment:
                    db.add(PostComment(post_id=post.id, user_id=user.id, body=body))


async def _ensure_message(
    db: AsyncSession, conversation: Conversation, sender: User, body: str
) -> Message:
    existing = await db.scalar(
        select(Message).where(
            Message.conversation_id == conversation.id,
            Message.sender_user_id == sender.id,
            Message.body == body,
        )
    )
    if existing:
        return existing
    return await messaging.send_in_conversation(db, sender, conversation.id, body)


async def _ensure_conversations(
    db: AsyncSession,
    users: dict[str, User],
    profiles: dict[str, CreatorProfile],
) -> None:
    pairs = (
        ("subscriber", "luna-sparks"),
        ("socialfan", "mira-nova"),
        ("newfan", "zara-pulse"),
        ("marketing-in", "sera-kim"),
    )
    for fan_local, creator_slug in pairs:
        fan = users[_email(fan_local)]
        creator = profiles[creator_slug]
        creator_user = users[_email(creator_slug)]
        conversation = await db.scalar(
            select(Conversation).where(
                Conversation.creator_id == creator.id,
                Conversation.viewer_user_id == fan.id,
            )
        )
        initial_body = f"Hi {creator.display_name}, I loved the latest studio update."
        if not conversation:
            await messaging.send_message(db, fan, creator.id, initial_body)
            await db.flush()
            conversation = await db.scalar(
                select(Conversation).where(
                    Conversation.creator_id == creator.id,
                    Conversation.viewer_user_id == fan.id,
                )
            )
        assert conversation
        await _ensure_message(db, conversation, fan, initial_body)
        await _ensure_message(
            db,
            conversation,
            creator_user,
            "Thank you! I’m glad you’re here—there’s another demo update coming soon.",
        )
        await _ensure_message(
            db,
            conversation,
            fan,
            "Perfect, I’ll keep an eye on the feed. Thanks for replying!",
        )


async def _ensure_live_history(
    db: AsyncSession, users: dict[str, User], profiles: dict[str, CreatorProfile]
) -> None:
    creator = profiles["skye-live"]
    title = "Demo studio Q&A — safely ended"
    existing = await db.scalar(
        select(LiveRoom).where(LiveRoom.creator_id == creator.id, LiveRoom.title == title)
    )
    if not existing:
        creator_user = users[_email("skye-live")]
        room = await streaming.start_live(
            db,
            creator_user,
            title,
            LiveAccessMode.public,
            "An ended local-only room retained as streaming history.",
        )
        # Start and end occur inside the same uncommitted transaction.  No public
        # request can observe a fake active room, and the creator participant is
        # the authoritative broadcaster for the historical session.
        await streaming.end_live(db, creator_user, room.id)


async def _ensure_subscription_plans(db: AsyncSession, profiles: dict[str, CreatorProfile]) -> None:
    for position, seed in enumerate(PUBLIC_CREATORS):
        await subscriptions.configure_plan(
            db,
            profiles[seed.slug].id,
            "EUR",
            True,
            [
                {
                    "duration": "month_1",
                    "amount_minor": 999 + position * 50,
                    "enabled": True,
                },
                {
                    "duration": "month_3",
                    "amount_minor": 2_699 + position * 100,
                    "enabled": True,
                },
            ],
        )


async def _ensure_listings(
    db: AsyncSession,
    admin: User,
    users: dict[str, User],
    profiles: dict[str, CreatorProfile],
    content: dict[str, CreatorContent],
) -> list[MarketplaceListing]:
    rows: list[MarketplaceListing] = []
    for creator_position, seed in enumerate(PUBLIC_CREATORS):
        creator = profiles[seed.slug]
        creator_user = users[seed.email]
        for item_position in range(listing_count_for_creator(creator_position)):
            title = listing_title(seed, item_position)
            listing = await db.scalar(
                select(MarketplaceListing).where(
                    MarketplaceListing.owner_creator_id == creator.id,
                    MarketplaceListing.title == title,
                )
            )
            if not listing:
                listing = await marketplace.create_listing(
                    db,
                    creator_user,
                    creator_id=creator.id,
                    title=title,
                    description="A harmless fictional physical item for local marketplace testing.",
                    category="collectibles",
                    condition="new",
                    quantity_available=10,
                    price_amount_minor=1_500 + creator_position * 100 + item_position * 250,
                    currency="EUR",
                    shipping_mode="worldwide",
                    origin_country_code="PT",
                    shipping_charged_minor=350,
                    media_asset_ids=[content[seed.slug].image_asset.id],
                )
            if listing.status in {
                MarketplaceListingStatus.draft,
                MarketplaceListingStatus.paused,
            }:
                await marketplace.submit_listing_for_review(
                    db, creator_user, listing.id, creator.id
                )
            if listing.status is MarketplaceListingStatus.pending_review:
                await marketplace.decide_listing_moderation(db, admin, listing.id, True)
            rows.append(listing)
    return rows


async def _settle_attempt(db: AsyncSession, attempt: PaymentAttempt) -> None:
    if attempt.status is not PaymentStatus.pending:
        return
    payload, signature = finance.development_webhook_payload(attempt)
    await finance.process_development_webhook(db, payload, signature)


async def _ensure_marketplace_configuration(db: AsyncSession, admin: User) -> None:
    hold = await db.scalar(
        select(MarketplaceEarningsHoldPolicy).where(
            MarketplaceEarningsHoldPolicy.seller_tier == MarketplaceSellerTier.new_seller
        )
    )
    if not hold or not hold.active or not hold.is_default or hold.hold_duration_seconds != 0:
        await marketplace.configure_hold_policy(
            db,
            admin,
            tier_value=MarketplaceSellerTier.new_seller.value,
            hold_duration_seconds=0,
            active=True,
            is_default=True,
        )
    allowance = await db.scalar(
        select(MarketplaceShippingAllowance).where(
            MarketplaceShippingAllowance.scope == ShippingAllowanceScope.global_,
            MarketplaceShippingAllowance.destination_code == "*",
            MarketplaceShippingAllowance.currency == "EUR",
        )
    )
    if not allowance or not allowance.active or allowance.allowed_shipping_minor != 500:
        await marketplace.configure_shipping_allowance(
            db,
            admin,
            country_code=None,
            region_code=None,
            currency="EUR",
            allowed_shipping_minor=500,
        )


async def _ensure_financial_examples(
    db: AsyncSession,
    admin: User,
    users: dict[str, User],
    profiles: dict[str, CreatorProfile],
    content: dict[str, CreatorContent],
    listings: list[MarketplaceListing],
) -> None:
    subscription_pairs = (
        ("subscriber", "ivy-ember"),
        ("fan01", "luna-sparks"),
        ("fan02", "mira-nova"),
        ("fan03", "aria-group"),
        ("fan04", "valentina-cruz"),
        ("marketing-in", "sera-kim"),
    )
    for fan_local, creator_slug in subscription_pairs:
        subscription = await subscriptions.create_subscription(
            db,
            users[_email(fan_local)],
            profiles[creator_slug].id,
            "month_1",
            f"demo-subscription-{fan_local}-{creator_slug}",
        )
        period = await db.scalar(
            select(SubscriptionPeriod).where(
                SubscriptionPeriod.subscription_id == subscription.id,
                SubscriptionPeriod.sequence == 1,
            )
        )
        assert period
        attempt = await db.get(PaymentAttempt, period.payment_attempt_id)
        assert attempt
        await _settle_attempt(db, attempt)

    ppv_creators = [
        seed.slug
        for position, seed in enumerate(PUBLIC_CREATORS)
        if _video_policy(position, seed) is AccessPolicy.ppv
    ]
    ppv_buyers = ("ppvbuyer", "fan05", "fan06", "fan07", "fan08", "fan09", "fan10")
    for buyer_local, creator_slug in zip(ppv_buyers, ppv_creators, strict=False):
        purchase = await finance.initiate_purchase(
            db,
            users[_email(buyer_local)],
            content[creator_slug].video.id,
            f"demo-ppv-{buyer_local}-{creator_slug}",
        )
        attempt = await db.get(PaymentAttempt, purchase.payment_attempt_id)
        assert attempt
        await _settle_attempt(db, attempt)

    await _ensure_marketplace_configuration(db, admin)
    listing_by_creator = {
        creator_slug: next(
            listing for listing in listings if listing.owner_creator_id == profiles[creator_slug].id
        )
        for creator_slug in ("nora-market", "aria-group", "valentina-cruz")
    }
    order_pairs = (
        ("marketbuyer", "nora-market"),
        ("fan11", "aria-group"),
        ("fan12", "valentina-cruz"),
    )
    for position, (buyer_local, creator_slug) in enumerate(order_pairs):
        buyer = users[_email(buyer_local)]
        order = await marketplace.initiate_order(
            db,
            buyer,
            listing_by_creator[creator_slug].id,
            1,
            "PT",
            f"demo-marketplace-{buyer_local}-{creator_slug}",
            shipping_address={
                "recipient_name": "Fictional Demo Buyer",
                "line1": "1 Demo Street",
                "line2": None,
                "city": "Lisbon",
                "region_code": None,
                "postal_code": "1000-001",
                "country_code": "PT",
            },
        )
        attempt = await db.get(PaymentAttempt, order.payment_attempt_id)
        assert attempt
        await _settle_attempt(db, attempt)
        seller = users[_email(creator_slug)]
        if position == 0 and order.status is MarketplaceOrderStatus.paid:
            await marketplace.mark_order_processing(db, order.id, seller, profiles[creator_slug].id)
        if position == 0 and order.status is MarketplaceOrderStatus.processing:
            await marketplace.mark_order_shipped(
                db,
                order.id,
                seller,
                profiles[creator_slug].id,
                "Demo Carrier",
                "DEMO-TRACKING-001",
            )
        if position == 0 and order.status is MarketplaceOrderStatus.shipped:
            await marketplace.confirm_order_delivery(db, order.id, buyer)
            await marketplace.release_order_earnings(db, order)
        if position == 1 and order.status is MarketplaceOrderStatus.paid:
            await marketplace.mark_order_processing(db, order.id, seller, profiles[creator_slug].id)


async def _ensure_featuring(
    db: AsyncSession,
    admin: User,
    users: dict[str, User],
    profiles: dict[str, CreatorProfile],
) -> None:
    surface = await featuring.create_surface(
        db, admin, FeatureSurfaceKind.discover_creators.value, 0
    )
    slot = await db.scalar(
        select(FeatureSlot).where(
            FeatureSlot.surface_id == surface.id,
            FeatureSlot.slot_key == "demo-creator-hero",
        )
    )
    if not slot:
        slot = await featuring.create_slot(
            db, admin, surface.id, "demo-creator-hero", 0, capacity=1
        )
    duration_seconds = 30 * 24 * 60 * 60
    price = await db.scalar(
        select(FeaturePrice)
        .where(
            FeaturePrice.slot_id == slot.id,
            FeaturePrice.target_type == FeatureTargetType.creator,
            FeaturePrice.duration_seconds == duration_seconds,
            FeaturePrice.active.is_(True),
        )
        .order_by(FeaturePrice.version.desc())
    )
    if not price:
        await featuring.create_price(
            db,
            admin,
            slot.id,
            FeatureTargetType.creator.value,
            duration_seconds,
            2_500,
            "EUR",
        )
    creator = profiles["luna-sparks"]
    creator_user = users[_email("luna-sparks")]
    booking = await db.scalar(
        select(FeatureBooking).where(
            FeatureBooking.purchaser_user_id == creator_user.id,
            FeatureBooking.idempotency_key == "demo-feature-luna-sparks",
        )
    )
    if not booking:
        booking = await featuring.create_booking(
            db,
            actor=creator_user,
            purchaser=creator_user,
            slot_id=slot.id,
            target_type=FeatureTargetType.creator.value,
            target_id=creator.id,
            starts_at=datetime.now(UTC) + timedelta(seconds=5),
            duration_seconds=duration_seconds,
            idempotency_key="demo-feature-luna-sparks",
        )
    if booking.status is FeatureBookingStatus.awaiting_payment:
        attempt = await featuring.initiate_payment(db, booking, creator_user)
        await _settle_attempt(db, attempt)
    if booking.status is FeatureBookingStatus.scheduled:
        await featuring.activate_due_bookings(db, booking.starts_at + timedelta(seconds=1))


async def _seed_stats(db: AsyncSession, profiles: dict[str, CreatorProfile]) -> SeedStats:
    creator_ids = [profile.id for profile in profiles.values()]
    public_ids = [profiles[seed.slug].id for seed in PUBLIC_CREATORS]
    user_count = int(
        await db.scalar(select(func.count(User.id)).where(User.email.in_([u.email for u in USERS])))
        or 0
    )
    creator_count = int(
        await db.scalar(
            select(func.count(CreatorProfile.id)).where(CreatorProfile.id.in_(creator_ids))
        )
        or 0
    )
    post_count = int(
        await db.scalar(
            select(func.count(FeedPost.id)).where(
                FeedPost.creator_id.in_(public_ids),
                FeedPost.status == FeedPostStatus.published,
            )
        )
        or 0
    )
    content_count = int(
        await db.scalar(
            select(func.count(ContentItem.id)).where(
                ContentItem.owner_creator_id.in_(public_ids),
                ContentItem.status == ContentStatus.published,
            )
        )
        or 0
    )
    listing_count = int(
        await db.scalar(
            select(func.count(MarketplaceListing.id)).where(
                MarketplaceListing.owner_creator_id.in_(public_ids),
                MarketplaceListing.status == MarketplaceListingStatus.published,
            )
        )
        or 0
    )
    active_story_count = int(
        await db.scalar(
            select(func.count(Story.id)).where(
                Story.creator_id.in_(public_ids),
                Story.status == StoryStatus.active,
                Story.expires_at > datetime.now(UTC),
            )
        )
        or 0
    )
    return SeedStats(
        user_count,
        creator_count,
        post_count,
        content_count,
        listing_count,
        active_story_count,
    )


async def seed_database(
    db: AsyncSession,
    provider,
    *,
    asset_root: Path = ASSET_ROOT,
) -> SeedStats:
    """Converge a supplied transaction onto the local demo manifest.

    ``seed()`` owns the development guard.  This injected form is intentionally
    available to PostgreSQL integration tests using an isolated test database and
    an in-memory storage provider.
    """

    users = {seed.email: await _ensure_user(db, seed.email, seed.roles) for seed in USERS}
    admin = users[_email("admin")]
    profiles = {
        seed.slug: await _ensure_creator(db, admin, users[seed.email], seed) for seed in CREATORS
    }
    await _ensure_referral(db, users)
    await _ensure_groups(db, users, profiles)

    content: dict[str, CreatorContent] = {}
    posts: dict[str, list[FeedPost]] = {}
    for position, seed in enumerate(PUBLIC_CREATORS):
        content[seed.slug] = await _ensure_creator_content(
            db,
            admin,
            users[seed.email],
            profiles[seed.slug],
            seed,
            position,
            provider,
            asset_root,
        )
        posts[seed.slug] = await _ensure_posts(
            db,
            users[seed.email],
            profiles[seed.slug],
            seed,
            content[seed.slug],
        )
    await _ensure_stories(db, users, profiles, provider, asset_root)
    await _ensure_social_graph(db, users, profiles, posts)
    await _ensure_conversations(db, users, profiles)
    await _ensure_live_history(db, users, profiles)
    await _ensure_subscription_plans(db, profiles)
    listings = await _ensure_listings(db, admin, users, profiles, content)
    await _ensure_financial_examples(db, admin, users, profiles, content, listings)
    await _ensure_featuring(db, admin, users, profiles)
    await db.flush()
    return await _seed_stats(db, profiles)
