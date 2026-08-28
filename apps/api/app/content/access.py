from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.accounts.adult_access import AdultAccessDecision
from app.compliance.policy import effective_policy_for_country
from app.compliance.types import ComplianceDecision
from app.creators.service import (
    require_public_creator_access,
    resolve_creator_compliance_eligibility,
)
from app.media.contexts import has_single_media_context
from app.models.compliance import ComplianceFeature
from app.models.content import (
    AccessPolicy,
    ContentEntitlement,
    ContentItem,
    ContentStatus,
    DerivativeType,
    EntitlementStatus,
    Gallery,
    GalleryItem,
    MediaAsset,
    MediaAudience,
    MediaDerivative,
    MediaStatus,
    ModerationStatus,
    VideoContent,
)
from app.models.creator import CreatorProfile, CreatorStatus
from app.models.identity import User
from app.models.messaging import UserBlock
from app.models.social import Follow

HIDDEN_MODERATION = {"flagged", "rejected", "removed"}


async def asset_delivery_feature(db: AsyncSession, asset_id: UUID) -> ComplianceFeature:
    """Resolve the most restrictive owning surface for an opaque derivative URL."""
    from app.models.marketplace import MarketplaceListingMedia
    from app.models.messaging import MessageAttachment

    if await db.scalar(
        select(MessageAttachment.id).where(MessageAttachment.media_asset_id == asset_id)
    ):
        return ComplianceFeature.messaging
    if await db.scalar(
        select(MarketplaceListingMedia.id).where(MarketplaceListingMedia.media_asset_id == asset_id)
    ):
        return ComplianceFeature.marketplace
    asset = await db.get(MediaAsset, asset_id)
    if asset and asset.audience is MediaAudience.adult_restricted:
        return ComplianceFeature.adult_media
    return ComplianceFeature.platform_access


async def public_content_eligible(
    db: AsyncSession,
    content: ContentItem,
    user: User | None = None,
    adult_decision: AdultAccessDecision | None = None,
) -> bool:
    """Resolve whether a content object may appear on a consumer surface.

    This is deliberately independent from entitlement. Passing an age/access
    acknowledgement or buying content must never make a suspended creator,
    contained content, a withdrawn consent release, or a blocked relationship
    publicly eligible again.
    """
    if not await public_content_surface_eligible(db, content, user):
        return False
    return not await content_requires_adult_access(db, content) or bool(
        adult_decision and adult_decision.allowed
    )


async def public_content_surface_eligible(
    db: AsyncSession, content: ContentItem, user: User | None = None
) -> bool:
    """Resolve non-entitlement, non-age public serving prerequisites."""
    if (
        content.status is not ContentStatus.published
        or content.moderation_status.name != "approved"
    ):
        return False
    from app.trust_safety.service import (
        has_verified_content_performers,
        valid_verified_release_for_content,
    )

    if (
        content.requires_verified_consent or await has_verified_content_performers(db, content.id)
    ) and not await valid_verified_release_for_content(db, content.id):
        return False
    creator = await db.get(CreatorProfile, content.owner_creator_id)
    if not creator or creator.status is not CreatorStatus.approved or not creator.is_public:
        return False
    if not (await resolve_creator_compliance_eligibility(db, profile=creator)).public_allowed:
        return False
    if user and creator.user_id != user.id:
        blocked = await db.scalar(
            select(UserBlock.id).where(
                or_(
                    (UserBlock.blocker_user_id == user.id)
                    & (UserBlock.blocked_user_id == creator.user_id),
                    (UserBlock.blocker_user_id == creator.user_id)
                    & (UserBlock.blocked_user_id == user.id),
                )
            )
        )
        if blocked:
            return False
    return True


async def content_requires_adult_access(db: AsyncSession, content: ContentItem) -> bool:
    audiences = await db.scalars(
        select(MediaAsset.audience)
        .outerjoin(VideoContent, VideoContent.source_media_asset_id == MediaAsset.id)
        .outerjoin(GalleryItem, GalleryItem.media_asset_id == MediaAsset.id)
        .outerjoin(Gallery, Gallery.id == GalleryItem.gallery_id)
        .where(
            or_(
                VideoContent.content_id == content.id,
                Gallery.content_id == content.id,
            )
        )
    )
    values = list(audiences)
    return not values or any(value is MediaAudience.adult_restricted for value in values)


def adult_access_allows_asset(
    asset: MediaAsset,
    adult_decision: AdultAccessDecision | ComplianceDecision | None,
) -> bool:
    if isinstance(adult_decision, ComplianceDecision):
        # A canonical decision includes platform/domain feature availability as
        # well as age. Safe media must not bypass a blocked jurisdiction.
        return adult_decision.allowed
    return asset.audience is MediaAudience.safe_public or bool(
        adult_decision and adult_decision.allowed
    )


async def can_access_content(db: AsyncSession, content: ContentItem, user: User | None) -> bool:
    """Fail closed for all non-free policies; routes must use this resolver."""
    if (
        content.status is not ContentStatus.published
        or content.moderation_status.name in HIDDEN_MODERATION
    ):
        return False
    from app.trust_safety.service import (
        has_verified_content_performers,
        valid_verified_release_for_content,
    )

    if (
        content.requires_verified_consent or await has_verified_content_performers(db, content.id)
    ) and not await valid_verified_release_for_content(db, content.id):
        return False
    if user:
        owner = await db.scalar(select(CreatorProfile.id).where(CreatorProfile.user_id == user.id))
        if owner == content.owner_creator_id:
            return True
    if content.access_policy is AccessPolicy.free:
        return True
    if not user:
        return False
    if content.access_policy is AccessPolicy.followers:
        return (
            await db.scalar(
                select(Follow.id).where(
                    Follow.user_id == user.id, Follow.creator_id == content.owner_creator_id
                )
            )
            is not None
        )
    now = datetime.now(UTC)
    scope = ContentEntitlement.content_id == content.id
    if content.access_policy is AccessPolicy.subscription:
        scope = ContentEntitlement.creator_id == content.owner_creator_id
    entitlement = await db.scalar(
        select(ContentEntitlement.id).where(
            ContentEntitlement.subject_user_id == user.id,
            scope,
            ContentEntitlement.status == EntitlementStatus.active,
            ContentEntitlement.valid_from <= now,
            or_(ContentEntitlement.valid_until.is_(None), ContentEntitlement.valid_until > now),
        )
    )
    return entitlement is not None


async def can_access_asset(
    db: AsyncSession,
    asset_id: UUID,
    user: User | None,
    adult_decision: AdultAccessDecision | ComplianceDecision | None = None,
) -> bool:
    """Only an owning or entitled published content item can authorize full media."""
    asset = await db.get(MediaAsset, asset_id)
    if (
        not asset
        or asset.status is not MediaStatus.ready
        or asset.deleted_at is not None
        or asset.moderation_status.name in {"flagged", "rejected", "removed"}
        or not adult_access_allows_asset(asset, adult_decision)
    ):
        return False
    if not await has_single_media_context(db, asset_id):
        return False
    contents = (
        (
            await db.scalars(
                select(ContentItem)
                .outerjoin(VideoContent, VideoContent.content_id == ContentItem.id)
                .outerjoin(Gallery, Gallery.content_id == ContentItem.id)
                .outerjoin(GalleryItem, GalleryItem.gallery_id == Gallery.id)
                .where(
                    or_(
                        VideoContent.source_media_asset_id == asset_id,
                        GalleryItem.media_asset_id == asset_id,
                    )
                )
            )
        )
        .unique()
        .all()
    )
    for content in contents:
        owner_access = False
        if user:
            owner = await db.scalar(
                select(CreatorProfile.id).where(CreatorProfile.user_id == user.id)
            )
            owner_access = owner == content.owner_creator_id
        if (
            content.owner_creator_id == asset.owner_creator_id
            and await can_access_content(db, content, user)
            and (owner_access or await public_content_eligible(db, content, user, adult_decision))
        ):
            return True
    # A message offer is a distinct entitlement source: buying it must not
    # unlock unrelated gallery/video content that happens to share a creator.
    if user:
        from app.models.messaging import (
            Conversation,
            Message,
            MessageAttachment,
            MessageUnlockPurchase,
        )

        attachment_owner = await db.scalar(
            select(MessageAttachment.id)
            .join(Message, Message.id == MessageAttachment.message_id)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .join(CreatorProfile, CreatorProfile.id == Conversation.creator_id)
            .where(
                MessageAttachment.media_asset_id == asset_id,
                CreatorProfile.user_id == user.id,
            )
        )
        if attachment_owner:
            return True

        unlock_creator_id = await db.scalar(
            select(MessageUnlockPurchase.seller_creator_id)
            .join(
                MessageAttachment,
                MessageAttachment.id == MessageUnlockPurchase.message_attachment_id,
            )
            .where(
                MessageAttachment.media_asset_id == asset_id,
                MessageUnlockPurchase.buyer_user_id == user.id,
                MessageUnlockPurchase.status == "paid",
            )
        )
        if unlock_creator_id:
            try:
                await require_public_creator_access(db, unlock_creator_id, user.id)
            except ValueError:
                return False
            return True
    from app.models.social import FeedPost, FeedPostMedia
    from app.social.service import can_access_post

    feed_post = await db.scalar(
        select(FeedPost)
        .join(FeedPostMedia, FeedPostMedia.post_id == FeedPost.id)
        .where(
            FeedPostMedia.media_asset_id == asset_id,
            FeedPost.creator_id == asset.owner_creator_id,
        )
    )
    return bool(feed_post and await can_access_post(db, feed_post, user, adult_decision))


async def can_access_preview(
    db: AsyncSession,
    derivative: MediaDerivative,
    user: User | None = None,
    adult_decision: AdultAccessDecision | ComplianceDecision | None = None,
) -> bool:
    """A preview is public only when it belongs to published, ready content and is configured."""
    if derivative.status is not MediaStatus.ready:
        return False
    if not await has_single_media_context(db, derivative.media_asset_id):
        return False
    if isinstance(adult_decision, ComplianceDecision):
        # Preview publication is an explicit jurisdiction policy choice, separate
        # from viewer age assurance and content entitlement. Canonical public
        # routes always supply a decision; missing reviewed policy therefore
        # fails closed instead of treating the creator-configured teaser as public.
        if not adult_decision.jurisdiction:
            return False
        policy = await effective_policy_for_country(db, adult_decision.jurisdiction)
        if policy is None or not policy.rules.explicit_public_preview_allowed:
            return False
    asset = await db.get(MediaAsset, derivative.media_asset_id)
    if (
        not asset
        or asset.status is not MediaStatus.ready
        or asset.deleted_at is not None
        or asset.moderation_status.name in {"flagged", "rejected", "removed"}
        or not adult_access_allows_asset(asset, adult_decision)
    ):
        return False
    video = await db.scalar(
        select(VideoContent).where(VideoContent.source_media_asset_id == asset.id)
    )
    if video:
        content = await db.get(ContentItem, video.content_id)
        if (
            content
            and content.access_policy is not AccessPolicy.free
            and asset.duration_seconds is not None
            and (
                video.preview_start_seconds + video.preview_duration_seconds
                >= asset.duration_seconds
                or (
                    derivative.duration_seconds is not None
                    and derivative.duration_seconds >= asset.duration_seconds
                )
            )
        ):
            return False
        return bool(
            content
            and content.owner_creator_id == asset.owner_creator_id
            and await public_content_surface_eligible(db, content, user)
            and derivative.derivative_type in {DerivativeType.poster, DerivativeType.preview_clip}
        )
    row = await db.execute(
        select(ContentItem, Gallery, GalleryItem)
        .join(Gallery, Gallery.content_id == ContentItem.id)
        .join(GalleryItem, GalleryItem.gallery_id == Gallery.id)
        .where(GalleryItem.media_asset_id == asset.id)
    )
    for content, gallery, item in row:
        configured = item.is_preview or item.position < gallery.preview_count
        if (
            content.owner_creator_id != asset.owner_creator_id
            or not await public_content_surface_eligible(db, content, user)
        ):
            continue
        if configured and derivative.derivative_type is DerivativeType.blurred_preview:
            return True
        if content.access_policy is AccessPolicy.free and derivative.derivative_type in {
            DerivativeType.thumbnail,
            DerivativeType.display,
        }:
            return True
    from app.marketplace.service import listing_media_is_public_exclusive
    from app.models.marketplace import (
        MarketplaceListing,
        MarketplaceListingMedia,
        MarketplaceListingStatus,
    )

    listing = await db.scalar(
        select(MarketplaceListing)
        .join(
            MarketplaceListingMedia,
            MarketplaceListingMedia.listing_id == MarketplaceListing.id,
        )
        .join(CreatorProfile, CreatorProfile.id == MarketplaceListing.owner_creator_id)
        .where(
            MarketplaceListingMedia.media_asset_id == asset.id,
            MarketplaceListing.owner_creator_id == asset.owner_creator_id,
            MarketplaceListing.status == MarketplaceListingStatus.published,
            MarketplaceListing.moderation_status == ModerationStatus.approved,
            CreatorProfile.status == CreatorStatus.approved,
            CreatorProfile.is_public.is_(True),
        )
    )
    if (
        listing
        and await listing_media_is_public_exclusive(db, asset.id)
        and derivative.derivative_type
        in {
            DerivativeType.thumbnail,
            DerivativeType.display,
        }
    ):
        creator = await db.get(CreatorProfile, listing.owner_creator_id)
        if (
            not creator
            or not (
                await resolve_creator_compliance_eligibility(db, profile=creator)
            ).public_allowed
        ):
            return False
        if creator and user and creator.user_id != user.id:
            blocked = await db.scalar(
                select(UserBlock.id).where(
                    or_(
                        (UserBlock.blocker_user_id == user.id)
                        & (UserBlock.blocked_user_id == creator.user_id),
                        (UserBlock.blocker_user_id == creator.user_id)
                        & (UserBlock.blocked_user_id == user.id),
                    )
                )
            )
            if blocked:
                return False
        return True
    return False
