"use client";

import Image from "next/image";
import Link from "next/link";
import { MouseEvent, useEffect, useRef, useState } from "react";

import { api, ApiError } from "../lib/api";
import { mediaForUsername } from "../lib/demo-personas";
import {
  DiscoveryResult,
  MarketplaceListing,
  PublicSubscriptionOption,
  creatorUsernameFor,
  formatMoney,
  previewUrl,
} from "../lib/public-api";
import { AccessBadge, CreatorAvatar, VerifiedBadge, useLoginGate } from "./consumer-ui";
import styles from "./consumer-cards.module.css";

export function CreatorCard({ item, verified = true }: { item: DiscoveryResult; verified?: boolean }) {
  const username = creatorUsernameFor(item);
  const media = mediaForUsername(username);
  const [option, setOption] = useState<PublicSubscriptionOption | null>(null);
  const [following, setFollowing] = useState(false);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState("");
  const gate = useLoginGate();

  useEffect(() => {
    if (!username) return;
    let active = true;
    api<PublicSubscriptionOption[]>(`/creators/${encodeURIComponent(username)}/subscription-options`)
      .then((options) => {
        if (active) setOption(options.find((value) => value.duration === "month_1") ?? options[0] ?? null);
      })
      .catch(() => undefined);
    return () => { active = false; };
  }, [username]);

  useEffect(() => {
    if (!gate.authenticated || !item.creator_id) return;
    let active = true;
    api<{ following: boolean }>(`/feed/creator/${item.creator_id}/follow-state`)
      .then((state) => { if (active) setFollowing(state.following); })
      .catch(() => undefined);
    return () => { active = false; };
  }, [gate.authenticated, item.creator_id]);

  async function toggleFollow(event: MouseEvent<HTMLButtonElement>) {
    event.preventDefault();
    event.stopPropagation();
    if (!item.creator_id || !gate.requireLogin()) return;
    setWorking(true);
    setMessage("");
    try {
      await api(`/feed/creator/${item.creator_id}/follow`, { method: following ? "DELETE" : "POST" });
      setFollowing((value) => !value);
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "Follow action unavailable");
    } finally {
      setWorking(false);
    }
  }

  const href = username ? `/creator/${encodeURIComponent(username)}` : "/creators";
  return (
    <article className={styles.creatorCard}>
      <Link aria-label={`View ${item.title}'s profile`} className={styles.cardLink} href={href} />
      <div className={styles.creatorMedia}>
        {media ? <Image alt="" fill sizes="(max-width: 700px) 76vw, 280px" src={media.portrait} /> : <span className={styles.mediaFallback} />}
        <div className={styles.topBadges}>
          {item.sponsored && <span aria-label="Sponsored placement" className={styles.sponsored}>Sponsored</span>}
          {item.live && <span className={styles.liveBadge}><span aria-hidden="true">●</span> Live</span>}
        </div>
      </div>
      <div className={styles.creatorBody}>
        <div className={styles.identityRow}>
          <CreatorAvatar displayName={item.title} live={item.live} size={44} username={username} />
          <div>
            <h3>{item.title} {verified && <VerifiedBadge />}</h3>
            <p>{username ? `@${username}` : item.subtitle}</p>
          </div>
        </div>
        {item.description && <p className={styles.description}>{item.description}</p>}
        <div className={styles.cardActions}>
          <button aria-pressed={following} className={styles.secondaryButton} disabled={working || gate.loading} onClick={toggleFollow} type="button">
            {working ? "Saving…" : following ? "Following" : gate.authenticated ? "Follow" : "Log in to follow"}
          </button>
          <Link className={styles.primaryButton} href={href} onClick={(event) => event.stopPropagation()}>
            Subscribe{option ? ` · ${formatMoney(option.effective_amount_minor, option.currency)}` : ""}
          </Link>
        </div>
        {message && <p className={styles.inlineError} role="status">{message}</p>}
      </div>
    </article>
  );
}

export function ContentCard({ item, compact = false }: { item: DiscoveryResult; compact?: boolean }) {
  const username = creatorUsernameFor(item);
  const localMedia = mediaForUsername(username);
  const safePreview = previewUrl(item.preview_asset_id);
  const profileHref = username ? `/creator/${encodeURIComponent(username)}` : "/discover";
  return (
    <article className={`${styles.contentCard} ${compact ? styles.compact : ""}`}>
      <Link aria-label={`View ${item.title}`} className={styles.cardLink} href={profileHref} />
      <div className={styles.contentMedia}>
        <SafePreviewMedia fallback={localMedia?.content} safePreview={safePreview} title={item.title} />
        <div className={styles.topBadges}>
          {item.sponsored && <span aria-label="Sponsored placement" className={styles.sponsored}>Sponsored</span>}
          <AccessBadge locked={item.locked} policy={item.access_policy} />
        </div>
        {item.entity_type === "video" && <span aria-hidden="true" className={styles.play}>▶</span>}
        {item.locked && <span className={styles.locked}>Private preview</span>}
      </div>
      <div className={styles.contentBody}>
        <div className={styles.contentIdentity}>
          <CreatorAvatar displayName={item.subtitle ?? item.title} size={34} username={username} />
          <p>{item.subtitle ?? (username ? `@${username}` : "Creator")}</p>
        </div>
        <h3>{item.title}</h3>
        {item.description && !compact && <p className={styles.description}>{item.description}</p>}
        {item.price_amount_minor != null && item.currency && (
          <strong className={styles.price}>{formatMoney(item.price_amount_minor, item.currency)}</strong>
        )}
      </div>
    </article>
  );
}

function SafePreviewMedia({ safePreview, fallback, title }: { safePreview?: string; fallback?: string; title: string }) {
  const anchor = useRef<HTMLSpanElement>(null);
  const [resolved, setResolved] = useState<{ kind: "image" | "video"; url: string } | null>(null);

  useEffect(() => {
    if (!safePreview || !anchor.current) return;
    const controller = new AbortController();
    let active = true;
    let objectUrl: string | undefined;
    const observer = new IntersectionObserver((entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return;
      observer.disconnect();
      void fetch(safePreview, { credentials: "include", signal: controller.signal })
        .then((response) => {
          if (!response.ok) throw new Error("Preview unavailable");
          return response.blob();
        })
        .then((blob) => {
          objectUrl = URL.createObjectURL(blob);
          if (!active) {
            URL.revokeObjectURL(objectUrl);
            objectUrl = undefined;
            return;
          }
          setResolved({ kind: blob.type.startsWith("video/") ? "video" : "image", url: objectUrl });
        })
        .catch(() => undefined);
    }, { rootMargin: "240px" });
    observer.observe(anchor.current);
    return () => {
      active = false;
      observer.disconnect();
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [safePreview]);

  return (
    <span className={styles.previewAnchor} ref={anchor}>
      {resolved?.kind === "video" ? (
        <video aria-label={`${title} preview`} muted playsInline preload="metadata" src={resolved.url} />
      ) : resolved?.kind === "image" ? (
        <img alt={`${title} preview`} src={resolved.url} />
      ) : fallback ? (
        <Image alt="" fill sizes="(max-width: 700px) 88vw, 420px" src={fallback} />
      ) : (
        <span className={styles.mediaFallback} />
      )}
    </span>
  );
}

export function MarketplaceCard({
  listing,
  creator,
  sponsored = false,
}: {
  listing: MarketplaceListing;
  creator?: DiscoveryResult;
  sponsored?: boolean;
}) {
  const username = creator ? creatorUsernameFor(creator) : undefined;
  const media = mediaForUsername(username);
  const soldOut = listing.quantity_available === 0 || listing.status === "sold_out";
  return (
    <article className={styles.marketCard}>
      <Link aria-label={`View ${listing.title}`} className={styles.cardLink} href={`/marketplace/${encodeURIComponent(listing.public_id)}`} />
      <div className={styles.marketMedia}>
        {media ? <Image alt="" fill sizes="(max-width: 700px) 76vw, 320px" src={media.content} /> : <span className={styles.mediaFallback} />}
        <span className={styles.marketCategory}>{listing.category.replaceAll("_", " ")}</span>
        {sponsored && <span aria-label="Sponsored placement" className={styles.marketSponsored}>Sponsored</span>}
        {soldOut && <span className={styles.soldOut}>Sold out</span>}
      </div>
      <div className={styles.marketBody}>
        <div className={styles.sellerRow}>
          <CreatorAvatar displayName={creator?.title ?? "Creator"} size={30} username={username} />
          <span>{creator?.title ?? "FanBackstage creator"}</span>
        </div>
        <h3>{listing.title}</h3>
        <div className={styles.priceRow}>
          <strong>{formatMoney(listing.price_amount_minor, listing.currency)}</strong>
          <span>{listing.condition.replaceAll("_", " ")}</span>
        </div>
      </div>
    </article>
  );
}

export function MarketplaceDiscoveryCard({ item }: { item: DiscoveryResult }) {
  const username = creatorUsernameFor(item);
  const media = mediaForUsername(username);
  return (
    <article className={styles.marketCard}>
      <div className={styles.marketMedia}>
        {media ? <Image alt="" fill sizes="(max-width: 700px) 76vw, 320px" src={media.content} /> : <span className={styles.mediaFallback} />}
        <span className={styles.marketCategory}>Creator marketplace</span>
        {item.sponsored && <span aria-label="Sponsored placement" className={styles.marketSponsored}>Sponsored</span>}
        <span className={styles.soldOut}>{item.availability === "sold_out" ? "Sold out" : "Details unavailable"}</span>
      </div>
      <div className={styles.marketBody}>
        <div className={styles.sellerRow}>
          <CreatorAvatar displayName={item.subtitle ?? "Creator"} size={30} username={username} />
          <span>{item.subtitle ?? "FanBackstage creator"}</span>
        </div>
        <h3>{item.title}</h3>
        {item.price_amount_minor != null && item.currency && <div className={styles.priceRow}><strong>{formatMoney(item.price_amount_minor, item.currency)}</strong><span>{item.availability?.replaceAll("_", " ") ?? "Unavailable"}</span></div>}
      </div>
    </article>
  );
}
