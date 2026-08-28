"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../lib/api";
import {
  PublicStory,
  StoryRailPage,
  groupStoriesByCreator,
  storyAgeLabel,
  storyProfilePath,
  storyRailPath,
} from "../lib/stories-api";
import { AccessBadge, CreatorAvatar, EmptyState, SectionHeader, Skeleton, VerifiedBadge } from "./consumer-ui";
import { AdultAccessGate } from "./adult-access-gate";
import { StoryRail } from "./story-experience";
import styles from "./stories-browser.module.css";

export function StoriesBrowser() {
  const [stories, setStories] = useState<PublicStory[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [paginationError, setPaginationError] = useState("");
  const [now, setNow] = useState(() => Date.now());
  const [access, setAccess] = useState<StoryRailPage | null>(null);

  useEffect(() => {
    let active = true;
    api<StoryRailPage>(storyRailPath())
      .then((page) => {
        if (!active) return;
        setStories(page.items);
        setNextCursor(page.next_cursor);
        setAccess(page);
      })
      .catch((caught: unknown) => {
        if (active) setError(caught instanceof ApiError ? caught.message : "Unable to load stories");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    setNow(Date.now());
  }, [stories]);

  useEffect(() => {
    const nextExpiry = stories
      .map((story) => Date.parse(story.expires_at))
      .filter((expiresAt) => Number.isFinite(expiresAt) && expiresAt > now)
      .sort((first, second) => first - second)[0];
    if (!nextExpiry) return;
    const timer = window.setTimeout(
      () => setNow(Date.now()),
      Math.max(25, nextExpiry - now + 25),
    );
    return () => window.clearTimeout(timer);
  }, [now, stories]);

  const groups = useMemo(() => groupStoriesByCreator(stories, now), [now, stories]);

  async function loadMore() {
    if (!nextCursor || loadingMore) return;
    setLoadingMore(true);
    setPaginationError("");
    try {
      const page = await api<StoryRailPage>(storyRailPath({ cursor: nextCursor }));
      setStories((current) => {
        const byId = new Map(current.map((story) => [story.id, story]));
        page.items.forEach((story) => byId.set(story.id, story));
        return [...byId.values()];
      });
      setNextCursor(page.next_cursor);
    } catch (caught) {
      setPaginationError(caught instanceof ApiError ? caught.message : "Unable to load more stories");
    } finally {
      setLoadingMore(false);
    }
  }

  if (loading) {
    return <div className={styles.loading}>{[0, 1, 2, 3].map((value) => <Skeleton key={value} />)}</div>;
  }
  if (error) return <EmptyState body={error} title="Stories are unavailable" />;
  if (access && !access.compliance_allowed) {
    return (
      <AdultAccessGate
        access={access}
        adultRestricted={false}
        feature="platform_access"
        onGranted={async () => {
          const page = await api<StoryRailPage>(storyRailPath());
          setStories(page.items);
          setNextCursor(page.next_cursor);
          setAccess(page);
        }}
        title="Stories"
      />
    );
  }
  if (!groups.length) {
    return (
      <EmptyState
        body="Only active Stories that the server authorizes for you appear here. Check back after creators publish a new moment."
        title="No active stories"
      />
    );
  }

  return (
    <div className={styles.browser}>
      <section aria-labelledby="story-rail-title" className={styles.railSection}>
        <SectionHeader
          body="Choose a creator, then use Left and Right to move, Home or End to jump, and Escape to close."
          eyebrow="Active now"
          id="story-rail-title"
          title="Today’s stories"
        />
        <StoryRail stories={stories} />
      </section>
      <section aria-labelledby="story-creators-title">
        <SectionHeader
          body="Every card below is backed by an active Story the server has authorized for your current account."
          eyebrow="From the community"
          id="story-creators-title"
          title="Creators sharing now"
        />
        <div className={styles.grid}>
          {groups.map((group) => {
            const latest = group.stories[0];
            const policies = [...new Set(group.stories.map((story) => story.access_policy))];
            return (
              <article className={styles.creatorCard} key={group.creator.id}>
                <div aria-hidden="true" className={styles.cardGlow} />
                <div className={styles.creatorRow}>
                  <CreatorAvatar
                    displayName={group.creator.display_name}
                    size={58}
                    username={group.creator.username}
                  />
                  <div>
                    <h2>{group.creator.display_name} {group.creator.verified && <VerifiedBadge />}</h2>
                    <p>@{group.creator.username} · {storyAgeLabel(latest.published_at)}</p>
                  </div>
                </div>
                <p className={styles.caption}>{latest.caption ?? "No caption added."}</p>
                <div className={styles.cardMeta}>
                  <span>{group.stories.length} active {group.stories.length === 1 ? "story" : "stories"}</span>
                  <span className={styles.policies}>
                    {policies.map((policy) => <AccessBadge key={policy} policy={policy} />)}
                  </span>
                </div>
                <Link className={styles.profileLink} href={storyProfilePath(group.creator.username)}>
                  View {group.creator.display_name.split(" ")[0]}’s profile <span aria-hidden="true">→</span>
                </Link>
              </article>
            );
          })}
        </div>
      </section>
      {(nextCursor || paginationError) && (
        <div className={styles.pagination}>
          {paginationError && <p role="alert">{paginationError}</p>}
          {nextCursor && (
            <button disabled={loadingMore} onClick={() => void loadMore()} type="button">
              {loadingMore ? "Loading…" : "Load more stories"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
