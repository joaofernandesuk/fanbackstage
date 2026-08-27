"use client";

import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../lib/api";
import { DiscoveryPage, DiscoveryResult, discoverySearchPath } from "../lib/public-api";
import { ContentCard } from "./consumer-cards";
import { EmptyState, Skeleton } from "./consumer-ui";
import styles from "./video-browser.module.css";

type AccessFilter = "all" | "free" | "subscription" | "ppv";

export function GalleryBrowser() {
  const [galleries, setGalleries] = useState<DiscoveryResult[]>([]);
  const [filter, setFilter] = useState<AccessFilter>("all");
  const [sort, setSort] = useState<"newest" | "trending">("newest");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [visibleLimit, setVisibleLimit] = useState(12);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    api<DiscoveryPage>(discoverySearchPath({ types: ["gallery"], sort, limit: 50 }))
      .then((page) => { if (active) setGalleries(page.items.filter((item) => item.entity_type === "gallery")); })
      .catch((caught: unknown) => { if (active) setError(caught instanceof ApiError ? caught.message : "Unable to load galleries"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [sort]);

  const visible = useMemo(() => galleries.filter((gallery) => {
    if (filter === "all") return true;
    if (filter === "subscription") return gallery.access_policy === "subscription" || gallery.access_policy === "subscribers";
    if (filter === "free") return !gallery.access_policy || gallery.access_policy === "free";
    return gallery.access_policy === filter;
  }), [filter, galleries]);

  return (
    <div className={styles.browser}>
      <div className={styles.controls}>
        <div aria-label="Gallery access" role="group">
          {(["all", "free", "subscription", "ppv"] as const).map((value) => (
            <button aria-pressed={filter === value} key={value} onClick={() => setFilter(value)} type="button">
              {value === "all" ? "All galleries" : value === "subscription" ? "Subscribers" : value === "ppv" ? "Premium / PPV" : "Free"}
            </button>
          ))}
        </div>
        <label>Sort
          <select onChange={(event) => setSort(event.target.value as typeof sort)} value={sort}>
            <option value="newest">Newest</option>
            <option value="trending">Trending</option>
          </select>
        </label>
      </div>
      {error && <div className={styles.error} role="alert">{error}</div>}
      {loading ? (
        <div className={styles.grid}>{[0, 1, 2, 3, 4, 5].map((value) => <Skeleton key={value} />)}</div>
      ) : visible.length ? (
        <>
          <div className={styles.grid}>{visible.slice(0, visibleLimit).map((gallery) => <ContentCard item={gallery} key={gallery.id} />)}</div>
          {visible.length > visibleLimit && <button className={styles.loadMore} onClick={() => setVisibleLimit((value) => value + 12)} type="button">Load more galleries</button>}
        </>
      ) : (
        <EmptyState body="Try another access filter, or return when creators publish new gallery drops." title="No galleries in this collection" />
      )}
    </div>
  );
}
