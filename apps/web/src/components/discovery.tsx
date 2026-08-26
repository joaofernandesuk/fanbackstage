"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../lib/api";
import {
  DiscoveryEntityType,
  DiscoveryPage,
  DiscoveryResult,
  MarketplaceListing,
  discoverPath,
  discoverySearchPath,
  groupDiscovery,
} from "../lib/public-api";
import { ContentCard, CreatorCard, MarketplaceCard, MarketplaceDiscoveryCard } from "./consumer-cards";
import { EmptyState, SectionHeader, Skeleton } from "./consumer-ui";
import { StoryRailSource } from "./story-experience";
import styles from "./discovery.module.css";

type Filter = "all" | "creator" | "video" | "marketplace_listing" | "live";

const filterTypes: Record<Filter, DiscoveryEntityType[] | undefined> = {
  all: undefined,
  creator: ["creator"],
  video: ["video"],
  marketplace_listing: ["marketplace_listing"],
  live: ["creator", "live_room"],
};

export function Discovery({ initialQuery = "" }: { initialQuery?: string }) {
  const [query, setQuery] = useState(initialQuery);
  const [submittedQuery, setSubmittedQuery] = useState(initialQuery);
  const [filter, setFilter] = useState<Filter>("all");
  const [sort, setSort] = useState<"trending" | "newest">("trending");
  const [page, setPage] = useState<DiscoveryPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [marketplaceListings, setMarketplaceListings] = useState<MarketplaceListing[]>([]);

  useEffect(() => {
    let active = true;
    api<MarketplaceListing[]>("/marketplace/listings")
      .then((listings) => { if (active) setMarketplaceListings(listings); })
      .catch(() => undefined);
    return () => { active = false; };
  }, []);

  const load = useCallback(async (cursor?: string | null) => {
    cursor ? setLoadingMore(true) : setLoading(true);
    setError("");
    try {
      const shouldSearch = Boolean(submittedQuery.trim()) || filter !== "all" || sort !== "trending";
      const path = shouldSearch
        ? discoverySearchPath({
            query: submittedQuery,
            types: filterTypes[filter],
            cursor,
            liveNow: filter === "live",
            sort: filter === "live" ? "live" : sort,
            limit: 50,
          })
        : discoverPath(cursor, 50);
      const next = await api<DiscoveryPage>(path);
      setPage((current) => cursor && current ? { ...next, items: [...current.items, ...next.items] } : next);
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to load discovery");
      if (!cursor) setPage(null);
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, [filter, sort, submittedQuery]);

  useEffect(() => { void load(); }, [load]);

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = query.trim();
    if (trimmed.length === 1) {
      setError("Enter at least two characters to search.");
      return;
    }
    setSubmittedQuery(trimmed);
  }

  const grouped = useMemo(() => groupDiscovery(page?.items ?? []), [page]);
  const marketplaceContent = grouped.marketplace;
  const marketplaceById = useMemo(() => new Map(marketplaceListings.map((listing) => [listing.id, listing])), [marketplaceListings]);
  const liveResults = useMemo(() => {
    const byCreator = new Map<string, DiscoveryResult>();
    for (const item of grouped.live) {
      const key = item.creator_id ?? item.id;
      const normalized: DiscoveryResult = item.entity_type === "live_room" ? {
        ...item,
        entity_type: "creator",
        id: key,
        title: item.subtitle ?? item.creator_username ?? "Live creator",
        subtitle: item.creator_username ? `@${item.creator_username}` : item.subtitle,
        description: item.description ? `${item.title} — ${item.description}` : item.title,
      } : item;
      if (!byCreator.has(key) || item.entity_type === "creator") byCreator.set(key, normalized);
    }
    return [...byCreator.values()];
  }, [grouped.live]);

  return (
    <div className={styles.discovery}>
      <form className={styles.controls} onSubmit={submit} role="search">
        <label className={styles.searchLabel}>
          <span className="sr-only">Search FanBackstage</span>
          <span aria-hidden="true" className={styles.searchIcon}>⌕</span>
          <input minLength={2} onChange={(event) => setQuery(event.target.value)} placeholder="Search creators, videos and marketplace" value={query} />
          <button type="submit">Search</button>
        </label>
        <div aria-label="Discovery type" className={styles.filters} role="group">
          {(["all", "creator", "video", "marketplace_listing", "live"] as const).map((value) => (
            <button aria-pressed={filter === value} key={value} onClick={() => setFilter(value)} type="button">
              {value === "marketplace_listing" ? "Marketplace" : value === "creator" ? "Creators" : value === "video" ? "Videos" : value === "all" ? "For you" : "Live"}
            </button>
          ))}
          <label className={styles.sortLabel}>Sort
            <select onChange={(event) => setSort(event.target.value as "trending" | "newest")} value={sort}>
              <option value="trending">Trending</option>
              <option value="newest">Newest</option>
            </select>
          </label>
        </div>
      </form>

      {error && <div className={styles.error} role="alert"><p>{error}</p><button onClick={() => void load()} type="button">Try again</button></div>}

      {loading ? (
        <div className={styles.loadingGrid}>{[0, 1, 2, 3, 4, 5].map((value) => <Skeleton key={value} />)}</div>
      ) : page && page.items.length ? (
        <>
          {filter === "all" && (
            <section aria-labelledby="discover-stories">
              <SectionHeader eyebrow="Quick look" href="/stories" id="discover-stories" title="Stories" />
              <StoryRailSource limit={12} />
            </section>
          )}
          {liveResults.length > 0 && (
            <DiscoverySection eyebrow="On air" id="discover-live" title="Live now">
              {liveResults.slice(0, filter === "live" ? 50 : 4).map((item) => <CreatorCard item={item} key={`${item.entity_type}-${item.id}`} />)}
            </DiscoverySection>
          )}
          {grouped.creators.length > 0 && filter !== "live" && (
            <DiscoverySection eyebrow="People to know" id="discover-creators" title={submittedQuery ? "Creators" : "Featured creators"}>
              {grouped.creators.map((item) => <CreatorCard item={item} key={item.id} />)}
            </DiscoverySection>
          )}
          {grouped.content.length > 0 && filter !== "creator" && filter !== "live" && (
            <DiscoverySection content eyebrow="Fresh drops" id="discover-content" title={filter === "video" ? "Videos" : "Trending content"}>
              {grouped.content.map((item) => <ContentCard item={item} key={`${item.entity_type}-${item.id}`} />)}
            </DiscoverySection>
          )}
          {marketplaceContent.length > 0 && (
            <DiscoverySection content eyebrow="Creator-owned" id="discover-market" title="Marketplace finds">
              {marketplaceContent.map((item) => {
                const listing = marketplaceById.get(item.id);
                if (!listing) return <MarketplaceDiscoveryCard item={item} key={item.id} />;
                const creator: DiscoveryResult = {
                  ...item,
                  entity_type: "creator",
                  title: item.subtitle ?? item.creator_username ?? "FanBackstage creator",
                };
                return <MarketplaceCard creator={creator} key={item.id} listing={listing} sponsored={item.sponsored} />;
              })}
            </DiscoverySection>
          )}
          {page.next_cursor && (
            <div className={styles.loadMore}>
              <button disabled={loadingMore} onClick={() => void load(page.next_cursor)} type="button">{loadingMore ? "Loading…" : "Load more"}</button>
            </div>
          )}
        </>
      ) : (
        <EmptyState action={<Link className={styles.emptyLink} href="/creators">Browse all creators</Link>} body="Try a different search or remove a filter. Only eligible public results appear here." title="No public results found" />
      )}
    </div>
  );
}

function DiscoverySection({
  children,
  content = false,
  eyebrow,
  id,
  title,
}: {
  children: React.ReactNode;
  content?: boolean;
  eyebrow: string;
  id: string;
  title: string;
}) {
  return (
    <section aria-labelledby={id}>
      <SectionHeader eyebrow={eyebrow} id={id} title={title} />
      <div className={`${styles.grid} ${content ? styles.contentGrid : ""}`}>{children}</div>
    </section>
  );
}
