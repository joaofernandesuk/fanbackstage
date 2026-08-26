"use client";

import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../lib/api";
import {
  DiscoveryPage,
  DiscoveryResult,
  MarketplaceListing,
  discoverySearchPath,
} from "../lib/public-api";
import { MarketplaceCard } from "./consumer-cards";
import { EmptyState, Skeleton } from "./consumer-ui";
import styles from "./marketplace-browser.module.css";

export function MarketplaceBrowser() {
  const [listings, setListings] = useState<MarketplaceListing[]>([]);
  const [creators, setCreators] = useState<DiscoveryResult[]>([]);
  const [category, setCategory] = useState("all");
  const [sort, setSort] = useState<"newest" | "price_asc" | "price_desc">("newest");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    Promise.allSettled([
      api<MarketplaceListing[]>("/marketplace/listings"),
      api<DiscoveryPage>(discoverySearchPath({ types: ["creator"], limit: 50 })),
    ]).then(([listingResult, creatorResult]) => {
      if (!active) return;
      if (listingResult.status === "fulfilled") setListings(listingResult.value);
      else setError(listingResult.reason instanceof ApiError ? listingResult.reason.message : "Unable to load marketplace listings");
      if (creatorResult.status === "fulfilled") setCreators(creatorResult.value.items.filter((item) => item.entity_type === "creator"));
      setLoading(false);
    });
    return () => { active = false; };
  }, []);

  const creatorById = useMemo(() => new Map(creators.map((creator) => [creator.creator_id ?? creator.id, creator])), [creators]);
  const categories = useMemo(() => [...new Set(listings.map((listing) => listing.category))].sort(), [listings]);
  const visible = useMemo(() => listings
    .filter((listing) => category === "all" || listing.category === category)
    .sort((a, b) => {
      if (sort === "price_asc") return a.price_amount_minor - b.price_amount_minor;
      if (sort === "price_desc") return b.price_amount_minor - a.price_amount_minor;
      return 0;
    }), [category, listings, sort]);

  return (
    <div className={styles.browser}>
      <div className={styles.controls}>
        <label>Category
          <select onChange={(event) => setCategory(event.target.value)} value={category}>
            <option value="all">All categories</option>
            {categories.map((value) => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}
          </select>
        </label>
        <label>Sort
          <select onChange={(event) => setSort(event.target.value as typeof sort)} value={sort}>
            <option value="newest">Newest</option>
            <option value="price_asc">Price: low to high</option>
            <option value="price_desc">Price: high to low</option>
          </select>
        </label>
        <p><strong>{loading ? "—" : visible.length}</strong> item{visible.length === 1 ? "" : "s"}</p>
      </div>
      {error && <div className={styles.error} role="alert">{error}</div>}
      {loading ? (
        <div className={styles.grid}>{[0, 1, 2, 3, 4, 5, 6, 7].map((value) => <Skeleton key={value} />)}</div>
      ) : visible.length ? (
        <div className={styles.grid}>{visible.map((listing) => <MarketplaceCard creator={creatorById.get(listing.owner_creator_id)} key={listing.id} listing={listing} />)}</div>
      ) : (
        <EmptyState body="Try another category. Only reviewed, published listings are shown." title="No creator finds here yet" />
      )}
    </div>
  );
}
