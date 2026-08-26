"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { CreatorMessageComposer } from "../../../components/creator-message-composer";
import {
  AccessBadge,
  CreatorAvatar,
  EmptyState,
  Skeleton,
  useLoginGate,
  VerifiedBadge,
} from "../../../components/consumer-ui";
import { Feed } from "../../../components/feed";
import { PrivateSessionRequest } from "../../../components/private-session-request";
import { StoryRailSource } from "../../../components/story-experience";
import styles from "../../../components/social-surface.module.css";
import { SubscriptionOptions } from "../../../components/subscription-options";
import { api, ApiError } from "../../../lib/api";
import { mediaForUsername } from "../../../lib/demo-personas";
import { formatMoney, type MarketplaceListing } from "../../../lib/public-api";

type TaxonomyItem = { id: string; code: string; label: string };
type Creator = {
  id: string;
  display_name: string;
  username: string;
  bio: string | null;
  avatar_reference: string | null;
  cover_reference: string | null;
  location: string | null;
  timezone: string | null;
  verified: boolean;
  follower_count: number;
  languages: TaxonomyItem[];
  categories: TaxonomyItem[];
  social_links: { label: string; url: string }[];
};

type Content = {
  id: string;
  content_type: string;
  title: string;
  description: string | null;
  locked: boolean;
  access_policy: string;
  price_amount_minor: number | null;
  price_currency: string | null;
  previews: { derivative_id: string; delivery_path: string }[];
};

type ProfileTab = "feed" | "photos" | "videos" | "premium" | "stories" | "marketplace";

const apiBase = process.env.NEXT_PUBLIC_FANBACKSTAGE_API_URL ?? "http://localhost:8000";

function compactNumber(value: number) {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function ContentGallery({
  content,
  creator,
  purchasing,
  onPurchase,
  fallback,
}: {
  content: Content[];
  creator: Creator;
  purchasing: string | null;
  onPurchase: (item: Content) => void;
  fallback: string;
}) {
  if (!content.length) {
    return <EmptyState body="This creator is preparing the next public drop." title="More content is coming" />;
  }

  return (
    <div className={styles.contentGrid}>
      {content.map((item) => {
        const preview = item.previews[0];
        const imageSource = preview ? `${apiBase}/api/v1${preview.delivery_path}` : fallback;
        return (
          <article className={styles.contentTile} key={item.id}>
            <div className={`${styles.contentTileMedia} ${item.locked ? styles.contentTileLocked : ""}`}>
              <Image
                alt={`${item.title} preview`}
                fill
                sizes="(max-width: 640px) 100vw, 350px"
                src={imageSource}
                unoptimized={Boolean(preview)}
              />
              <div className={styles.mediaBadges}><AccessBadge locked={item.locked} policy={item.access_policy} /></div>
              {item.content_type === "video" && !item.locked && <span aria-label="Video" className={styles.playButton}>▶</span>}
            </div>
            <div className={styles.contentTileInfo}>
              <h3>{item.title}</h3>
              {item.description && <p>{item.description}</p>}
              <div className={styles.contentTileActions}>
                <span>{item.content_type.replaceAll("_", " ")}</span>
                {item.locked && item.access_policy === "ppv" && item.price_amount_minor !== null && item.price_currency && (
                  <button
                    aria-label={`Unlock for ${formatMoney(item.price_amount_minor, item.price_currency)}`}
                    disabled={purchasing === item.id}
                    onClick={() => onPurchase(item)}
                    type="button"
                  >
                    {purchasing === item.id ? "Unlocking…" : `Unlock ${formatMoney(item.price_amount_minor, item.price_currency)}`}
                  </button>
                )}
              </div>
            </div>
          </article>
        );
      })}
    </div>
  );
}

export default function CreatorPage({ params }: { params: Promise<{ username: string }> }) {
  const { authenticated, loading: authLoading, requireLogin } = useLoginGate();
  const [creator, setCreator] = useState<Creator | null>(null);
  const [content, setContent] = useState<Content[]>([]);
  const [listings, setListings] = useState<MarketplaceListing[]>([]);
  const [tab, setTab] = useState<ProfileTab>("feed");
  const [following, setFollowing] = useState(false);
  const [messageOpen, setMessageOpen] = useState(false);
  const [error, setError] = useState("");
  const [purchasing, setPurchasing] = useState<string | null>(null);
  const subscribeRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let active = true;
    params
      .then(async ({ username }) => {
        const [profile, publishedContent, marketplace] = await Promise.all([
          api<Creator>(`/creators/${encodeURIComponent(username)}`),
          api<Content[]>(`/content/public/by-creator/${encodeURIComponent(username)}`),
          api<MarketplaceListing[]>("/marketplace/listings?limit=100").catch(() => []),
        ]);
        if (!active) return;
        setCreator(profile);
        setContent(publishedContent);
        setListings(marketplace.filter((item) => item.owner_creator_id === profile.id));
        const state = await api<{ following: boolean }>(`/feed/creator/${profile.id}/follow-state`).catch(() => ({ following: false }));
        if (active) setFollowing(state.following);
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof ApiError ? caught.message : "Creator not found");
      });
    return () => { active = false; };
  }, [params]);

  const visual = mediaForUsername(creator?.username);
  const fallbackContent = visual?.content || "/images/fanbackstage-hero.png";
  const selectedContent = useMemo(() => {
    if (tab === "photos") return content.filter((item) => item.content_type !== "video" && !item.locked);
    if (tab === "videos") return content.filter((item) => item.content_type === "video");
    if (tab === "premium") return content.filter((item) => item.locked || ["subscription", "ppv"].includes(item.access_policy));
    return content;
  }, [content, tab]);

  async function toggleFollow() {
    if (!creator || !requireLogin()) return;
    setError("");
    try {
      await api(`/feed/creator/${creator.id}/follow`, { method: following ? "DELETE" : "POST" });
      setFollowing((value) => !value);
      setCreator((current) => current ? {
        ...current,
        follower_count: Math.max(0, current.follower_count + (following ? -1 : 1)),
      } : current);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to update follow status");
    }
  }

  async function purchase(item: Content) {
    if (!creator || !requireLogin()) return;
    setPurchasing(item.id);
    setError("");
    try {
      const started = await api<{ payment_attempt_id: string }>(`/purchases/content/${item.id}`, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
      });
      await api(`/payments/development/${started.payment_attempt_id}/complete`, { method: "POST" });
      setContent(await api<Content[]>(`/content/public/by-creator/${encodeURIComponent(creator.username)}`));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Purchase could not be completed");
    } finally {
      setPurchasing(null);
    }
  }

  if (error && !creator) {
    return <EmptyState action={<Link className={styles.primaryLink} href="/creators">Browse creators</Link>} body={error} title="Creator not found" />;
  }
  if (!creator) {
    return <div className={styles.profileLoading}><Skeleton lines={3} /><Skeleton lines={2} /></div>;
  }

  const coverSource = creator.cover_reference || visual?.cover || "/images/fanbackstage-hero.png";
  const photoCount = content.filter((item) => item.content_type !== "video").length;
  const videoCount = content.filter((item) => item.content_type === "video").length;

  return (
    <div className={styles.profileShell}>
      <section className={styles.profileHero} aria-labelledby="creator-name">
        <div className={styles.profileCover}>
          <Image alt={`${creator.display_name} cover`} fill priority sizes="(max-width: 1200px) 100vw, 1180px" src={coverSource} />
        </div>
        <div className={styles.profileInfo}>
          <CreatorAvatar className={styles.profileAvatar} displayName={creator.display_name} size={130} username={creator.username} />
          <div className={styles.profileIdentity}>
            <div className={styles.profileNameRow}>
              <h1 id="creator-name">{creator.display_name}</h1>
              {creator.verified && <VerifiedBadge />}
            </div>
            <p className={styles.profileHandle}>@{creator.username}</p>
          </div>
          <div className={styles.profileActions}>
            <button aria-pressed={following} className={styles.followButton} disabled={authLoading} onClick={() => void toggleFollow()} type="button">{following ? "Following" : "Follow"}</button>
            <button aria-label="View membership options" className={styles.subscribeButton} onClick={() => requireLogin() && subscribeRef.current?.scrollIntoView({ behavior: "smooth", block: "center" })} type="button">Subscribe</button>
            <button className={styles.messageButton} onClick={() => requireLogin() && setMessageOpen((value) => !value)} type="button">Message</button>
          </div>
        </div>
        <div className={styles.profileBioGrid}>
          <div>
            <p className={styles.profileBio}>{creator.bio || `Welcome backstage with ${creator.display_name}. Follow for public updates and explore premium creator drops.`}</p>
            <div className={styles.profileMeta}>
              {creator.location && <span>⌖ {creator.location}</span>}
              {creator.categories.map((item) => <span key={item.id}>#{item.label}</span>)}
              {creator.languages.map((item) => <span key={item.id}>{item.label}</span>)}
            </div>
          </div>
          <div aria-label="Creator statistics" className={styles.profileStats}>
            <span className={styles.stat}><strong>{compactNumber(creator.follower_count)}</strong><small>Followers</small></span>
            <span className={styles.stat}><strong>{photoCount}</strong><small>Photos</small></span>
            <span className={styles.stat}><strong>{videoCount}</strong><small>Videos</small></span>
          </div>
        </div>
        <nav aria-label="Creator profile sections" className={styles.profileNav}>
          {(["feed", "photos", "videos", "premium", "stories", "marketplace"] as ProfileTab[]).map((value) => (
            <button aria-selected={tab === value} key={value} onClick={() => setTab(value)} role="tab" type="button">{value[0].toUpperCase() + value.slice(1)}</button>
          ))}
        </nav>
      </section>

      {error && <p className={styles.inlineMessage} role="status">{error}</p>}

      {messageOpen && authenticated && (
        <section className={styles.actionPanel}>
          <CreatorMessageComposer creatorId={creator.id} />
        </section>
      )}

      <div className={styles.profileContent}>
        <section aria-label="Creator content">
          {tab === "feed" && <Feed creatorId={creator.id} />}
          {(["photos", "videos", "premium"] as ProfileTab[]).includes(tab) && (
            <ContentGallery content={selectedContent} creator={creator} fallback={fallbackContent} onPurchase={(item) => void purchase(item)} purchasing={purchasing} />
          )}
          {tab === "stories" && (
            <StoryRailSource
              creatorUsername={creator.username}
              emptyBody={`${creator.display_name} has no active Stories available to you right now.`}
              limit={50}
            />
          )}
          {tab === "marketplace" && (
            listings.length ? (
              <div className={styles.contentGrid}>
                {listings.map((listing) => (
                  <Link className={styles.contentTile} href={`/marketplace/${listing.public_id}`} key={listing.id}>
                    <div className={styles.contentTileMedia}><Image alt={listing.title} fill sizes="350px" src={fallbackContent} /></div>
                    <div className={styles.contentTileInfo}><h3>{listing.title}</h3><p>{listing.description}</p><div className={styles.contentTileActions}><span>{listing.quantity_available} available</span><strong>{formatMoney(listing.price_amount_minor, listing.currency)}</strong></div></div>
                  </Link>
                ))}
              </div>
            ) : <EmptyState action={<Link className={styles.secondaryLink} href="/marketplace">Explore marketplace</Link>} body="No public products from this creator right now." title="The shop is between drops" />
          )}
        </section>

        <aside aria-label={`${creator.display_name} access options`} className={styles.profileSide}>
          <div className={styles.profileSideCard} ref={subscribeRef}>
            <p className="eyebrow">GET CLOSER</p>
            <SubscriptionOptions creatorId={creator.id} username={creator.username} />
          </div>
          <div className={styles.profileSideCard}>
            <h2>Private live</h2>
            <p>Request one-to-one time when this creator has private sessions enabled. Pricing and authorization are confirmed before a session begins.</p>
            {authenticated ? <PrivateSessionRequest creatorId={creator.id} /> : <Link className={styles.secondaryLink} href={`/login?next=${encodeURIComponent(`/creator/${creator.username}`)}`}>Log in to request</Link>}
          </div>
          <div className={styles.profileSideCard}>
            <h2>Explore safely</h2>
            <p>Premium originals are delivered only after the server confirms your entitlement. Public pages show approved previews.</p>
          </div>
        </aside>
      </div>
    </div>
  );
}
