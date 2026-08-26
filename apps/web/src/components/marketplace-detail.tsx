"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError, api } from "../lib/api";
import { mediaForUsername } from "../lib/demo-personas";
import {
  DiscoveryPage,
  DiscoveryResult,
  MarketplaceListing,
  creatorUsernameFor,
  discoverySearchPath,
  formatMoney,
} from "../lib/public-api";
import { CreatorAvatar, EmptyState, LoginGate, Skeleton, VerifiedBadge } from "./consumer-ui";
import styles from "./marketplace-detail.module.css";

export function MarketplaceDetail({ publicId }: { publicId: string }) {
  const [listing, setListing] = useState<MarketplaceListing | null>(null);
  const [creator, setCreator] = useState<DiscoveryResult | undefined>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    Promise.allSettled([
      api<MarketplaceListing>(`/marketplace/listings/${encodeURIComponent(publicId)}`),
      api<DiscoveryPage>(discoverySearchPath({ types: ["creator"], limit: 50 })),
    ]).then(([listingResult, creatorsResult]) => {
      if (!active) return;
      if (listingResult.status === "rejected") {
        setError(listingResult.reason instanceof ApiError ? listingResult.reason.message : "Marketplace listing not found");
      } else {
        setListing(listingResult.value);
        if (creatorsResult.status === "fulfilled") {
          setCreator(creatorsResult.value.items.find((item) => item.entity_type === "creator" && (item.creator_id ?? item.id) === listingResult.value.owner_creator_id));
        }
      }
      setLoading(false);
    });
    return () => { active = false; };
  }, [publicId]);

  if (loading) return <div className={styles.loading}><Skeleton lines={4} /><Skeleton lines={5} /></div>;
  if (error || !listing) return <EmptyState action={<Link className={styles.backButton} href="/marketplace">Back to marketplace</Link>} body={error || "This listing is not publicly available."} title="Listing unavailable" />;

  const username = creator ? creatorUsernameFor(creator) : undefined;
  const media = mediaForUsername(username);
  const soldOut = listing.quantity_available === 0 || listing.status === "sold_out";
  const shipping = listing.shipping_charged_minor === 0 ? "Shipping included" : `${formatMoney(listing.shipping_charged_minor, listing.currency)} shipping`;

  return (
    <div className={styles.detail}>
      <nav aria-label="Breadcrumb" className={styles.breadcrumb}><Link href="/marketplace">Marketplace</Link><span aria-hidden="true">/</span><span>{listing.title}</span></nav>
      <div className={styles.layout}>
        <section aria-label={`${listing.title} image`} className={styles.gallery}>
          {media ? <Image alt={`${listing.title} from ${creator?.title ?? "a FanBackstage creator"}`} fill priority sizes="(max-width: 850px) 100vw, 58vw" src={media.content} /> : <span className={styles.fallback} />}
          <span className={styles.category}>{listing.category.replaceAll("_", " ")}</span>
        </section>
        <section className={styles.summary}>
          <p className={styles.kicker}>Creator marketplace</p>
          <h1>{listing.title}</h1>
          <p className={styles.price}>{formatMoney(listing.price_amount_minor, listing.currency)}</p>
          <p className={styles.description}>{listing.description || "A creator-owned FanBackstage marketplace item."}</p>
          <dl className={styles.facts}>
            <div><dt>Condition</dt><dd>{listing.condition.replaceAll("_", " ")}</dd></div>
            <div><dt>Availability</dt><dd>{soldOut ? "Sold out" : `${listing.quantity_available} available`}</dd></div>
            <div><dt>Ships from</dt><dd>{listing.origin_country_code}</dd></div>
            <div><dt>Delivery</dt><dd>{shipping}</dd></div>
          </dl>
          {soldOut ? <button className={styles.disabled} disabled type="button">Sold out</button> : (
            <LoginGate className={styles.buyButton} label="Log in to view purchase options">
              <Link className={styles.buyButton} href="/marketplace/orders">View your marketplace orders</Link>
            </LoginGate>
          )}
          <p className={styles.purchaseNote}>Final totals and delivery eligibility are confirmed before any paid action.</p>
          {creator && username && (
            <Link className={styles.seller} href={`/creator/${encodeURIComponent(username)}`}>
              <CreatorAvatar displayName={creator.title} size={48} username={username} />
              <span><small>Sold by</small><strong>{creator.title} <VerifiedBadge /></strong><em>@{username}</em></span>
              <b aria-hidden="true">→</b>
            </Link>
          )}
        </section>
      </div>
    </div>
  );
}
