"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../lib/api";
import {
  DiscoveryPage,
  DiscoveryResult,
  PublicCreator,
  creatorUsernameFor,
  discoverySearchPath,
} from "../lib/public-api";
import { CreatorCard } from "./consumer-cards";
import { EmptyState, Skeleton } from "./consumer-ui";
import styles from "./creator-directory.module.css";

export function CreatorDirectory({ initialSort = "trending" }: { initialSort?: "trending" | "newest" | "live" }) {
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [sort, setSort] = useState<"trending" | "newest" | "live">(initialSort);
  const [category, setCategory] = useState("all");
  const [language, setLanguage] = useState("all");
  const [location, setLocation] = useState("all");
  const [creators, setCreators] = useState<DiscoveryResult[]>([]);
  const [profiles, setProfiles] = useState<Record<string, PublicCreator>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const page = await api<DiscoveryPage>(discoverySearchPath({
        query: submittedQuery,
        types: ["creator"],
        liveNow: sort === "live",
        sort: sort === "live" ? "live" : sort,
        limit: 50,
      }));
      const publicCreators = page.items.filter((item) => item.entity_type === "creator");
      setCreators(publicCreators);
      const fetched = await Promise.allSettled(publicCreators.map(async (item) => {
        const username = creatorUsernameFor(item);
        if (!username) throw new Error("Creator username unavailable");
        return api<PublicCreator>(`/creators/${encodeURIComponent(username)}`);
      }));
      const nextProfiles: Record<string, PublicCreator> = {};
      fetched.forEach((result) => {
        if (result.status === "fulfilled") nextProfiles[result.value.username] = result.value;
      });
      setProfiles(nextProfiles);
    } catch (caught) {
      setCreators([]);
      setError(caught instanceof ApiError ? caught.message : "Unable to load creators");
    } finally {
      setLoading(false);
    }
  }, [sort, submittedQuery]);

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

  const facets = useMemo(() => {
    const allProfiles = Object.values(profiles);
    return {
      categories: [...new Map(allProfiles.flatMap((profile) => profile.categories).map((item) => [item.code, item.label])).entries()].sort((a, b) => a[1].localeCompare(b[1])),
      languages: [...new Map(allProfiles.flatMap((profile) => profile.languages).map((item) => [item.code, item.label])).entries()].sort((a, b) => a[1].localeCompare(b[1])),
      locations: [...new Set(allProfiles.map((profile) => profile.location).filter((value): value is string => Boolean(value)))].sort(),
    };
  }, [profiles]);

  const visible = useMemo(() => creators.filter((creator) => {
    const username = creatorUsernameFor(creator);
    const profile = username ? profiles[username] : undefined;
    if (category !== "all" && !profile?.categories.some((item) => item.code === category)) return false;
    if (language !== "all" && !profile?.languages.some((item) => item.code === language)) return false;
    if (location !== "all" && profile?.location !== location) return false;
    return true;
  }), [category, creators, language, location, profiles]);

  return (
    <div className={styles.directory}>
      <form className={styles.controls} onSubmit={submit} role="search">
        <label className={styles.search}>Search creators
          <span><input minLength={2} onChange={(event) => setQuery(event.target.value)} placeholder="Name, username or bio" value={query} /><button>Search</button></span>
        </label>
        <label>Category
          <select onChange={(event) => setCategory(event.target.value)} value={category}>
            <option value="all">All categories</option>
            {facets.categories.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
          </select>
        </label>
        <label>Language
          <select onChange={(event) => setLanguage(event.target.value)} value={language}>
            <option value="all">All languages</option>
            {facets.languages.map(([code, label]) => <option key={code} value={code}>{label}</option>)}
          </select>
        </label>
        <label>Location
          <select onChange={(event) => setLocation(event.target.value)} value={location}>
            <option value="all">All locations</option>
            {facets.locations.map((value) => <option key={value}>{value}</option>)}
          </select>
        </label>
        <label>Sort
          <select onChange={(event) => setSort(event.target.value as typeof sort)} value={sort}>
            <option value="trending">Trending</option>
            <option value="newest">Newest</option>
            <option value="live">Live now</option>
          </select>
        </label>
      </form>

      <div className={styles.resultBar}>
        <p><strong>{loading ? "—" : visible.length}</strong> public creator{visible.length === 1 ? "" : "s"}</p>
        {(category !== "all" || language !== "all" || location !== "all") && <button onClick={() => { setCategory("all"); setLanguage("all"); setLocation("all"); }} type="button">Clear filters</button>}
      </div>

      {error && <div className={styles.error} role="alert"><p>{error}</p><button onClick={() => void load()} type="button">Try again</button></div>}
      {loading ? (
        <div className={styles.grid}>{[0, 1, 2, 3, 4, 5, 6, 7].map((value) => <Skeleton key={value} />)}</div>
      ) : visible.length ? (
        <div className={styles.grid}>{visible.map((creator) => <CreatorCard item={creator} key={creator.id} verified={profiles[creatorUsernameFor(creator) ?? ""]?.verified ?? true} />)}</div>
      ) : (
        <EmptyState body="Try a broader search, another filter, or browse the trending directory." title="No creators match these filters" />
      )}
    </div>
  );
}
