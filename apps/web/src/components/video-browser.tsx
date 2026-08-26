"use client";

import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../lib/api";
import { DiscoveryPage, DiscoveryResult, discoverySearchPath } from "../lib/public-api";
import { ContentCard } from "./consumer-cards";
import { EmptyState, Skeleton } from "./consumer-ui";
import styles from "./video-browser.module.css";

type AccessFilter = "all" | "free" | "subscription" | "ppv";

export function VideoBrowser() {
  const [videos, setVideos] = useState<DiscoveryResult[]>([]);
  const [filter, setFilter] = useState<AccessFilter>("all");
  const [sort, setSort] = useState<"newest" | "trending">("newest");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [visibleLimit, setVisibleLimit] = useState(12);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    api<DiscoveryPage>(discoverySearchPath({ types: ["video"], sort, limit: 50 }))
      .then((page) => { if (active) setVideos(page.items.filter((item) => item.entity_type === "video")); })
      .catch((caught: unknown) => { if (active) setError(caught instanceof ApiError ? caught.message : "Unable to load videos"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [sort]);

  const visible = useMemo(() => videos.filter((video) => {
    if (filter === "all") return true;
    if (filter === "subscription") return video.access_policy === "subscription" || video.access_policy === "subscribers";
    if (filter === "free") return !video.access_policy || video.access_policy === "free";
    return video.access_policy === filter;
  }), [filter, videos]);

  return (
    <div className={styles.browser}>
      <div className={styles.controls}>
        <div aria-label="Video access" role="group">
          {(["all", "free", "subscription", "ppv"] as const).map((value) => (
            <button aria-pressed={filter === value} key={value} onClick={() => setFilter(value)} type="button">
              {value === "all" ? "All videos" : value === "subscription" ? "Subscribers" : value === "ppv" ? "Premium / PPV" : "Free"}
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
          <div className={styles.grid}>{visible.slice(0, visibleLimit).map((video) => <ContentCard item={video} key={video.id} />)}</div>
          {visible.length > visibleLimit && <button className={styles.loadMore} onClick={() => setVisibleLimit((value) => value + 12)} type="button">Load more videos</button>}
        </>
      ) : (
        <EmptyState body="Try another access filter, or return when creators publish new video drops." title="No videos in this collection" />
      )}
    </div>
  );
}
