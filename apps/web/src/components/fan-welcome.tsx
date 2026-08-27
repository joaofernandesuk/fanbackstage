"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { api, ApiError } from "../lib/api";
import {
  creatorUsernameFor,
  DiscoveryPage,
  DiscoveryResult,
  discoverySearchPath,
} from "../lib/public-api";
import { CreatorAvatar, Skeleton, useLoginGate, VerifiedBadge } from "./consumer-ui";
import styles from "./fan-welcome.module.css";

type FollowState = Record<string, boolean>;

function creatorId(item: DiscoveryResult) {
  return item.creator_id ?? item.id;
}

export function FanWelcome() {
  const { authenticated, loading: authLoading, requireLogin } = useLoginGate();
  const [creators, setCreators] = useState<DiscoveryResult[]>([]);
  const [following, setFollowing] = useState<FollowState>({});
  const [working, setWorking] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    if (authLoading) return;
    if (!authenticated) {
      setLoading(false);
      return;
    }
    let active = true;
    api<DiscoveryPage>(discoverySearchPath({
      types: ["creator"],
      sort: "trending",
      limit: 12,
    }))
      .then(async (page) => {
        const suggestions = page.items
          .filter((item) => item.entity_type === "creator")
          .slice(0, 6);
        const states = await Promise.all(suggestions.map(async (item) => {
          const state = await api<{ following: boolean }>(
            `/feed/creator/${creatorId(item)}/follow-state`,
          ).catch(() => ({ following: false }));
          return [creatorId(item), state.following] as const;
        }));
        if (!active) return;
        setCreators(suggestions);
        setFollowing(Object.fromEntries(states));
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(caught instanceof ApiError
            ? caught.message
            : "Creator suggestions are unavailable right now.");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => { active = false; };
  }, [authLoading, authenticated]);

  const followedCount = useMemo(
    () => creators.filter((creator) => following[creatorId(creator)]).length,
    [creators, following],
  );

  async function toggleFollow(item: DiscoveryResult) {
    const id = creatorId(item);
    const currentlyFollowing = Boolean(following[id]);
    setWorking(id);
    setError("");
    try {
      await api(`/feed/creator/${id}/follow`, {
        method: currentlyFollowing ? "DELETE" : "POST",
      });
      setFollowing((current) => ({ ...current, [id]: !currentlyFollowing }));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Follow could not be updated.");
    } finally {
      setWorking(null);
    }
  }

  if (authLoading || loading) {
    return (
      <div className={styles.loading}>
        <Skeleton lines={2} />
        <Skeleton lines={2} />
        <Skeleton lines={2} />
      </div>
    );
  }

  if (!authenticated) {
    return (
      <section className={styles.signedOut}>
        <p className={styles.eyebrow}>WELCOME BACKSTAGE</p>
        <h1>Log in to shape your feed</h1>
        <p>Your creator follows belong to your account and are never inferred from a browser flag.</p>
        <button onClick={() => requireLogin({ nextPath: "/welcome" })} type="button">
          Log in to continue
        </button>
      </section>
    );
  }

  return (
    <div className={styles.welcome}>
      <header className={styles.intro}>
        <div>
          <p className={styles.eyebrow}>WELCOME BACKSTAGE</p>
          <h1>Start with creators who make this place feel alive.</h1>
          <p>
            These suggestions come from FanBackstage&apos;s existing public Discovery results.
            Follow a few to turn your Following feed into a useful first stop.
          </p>
        </div>
        <div aria-label={`${followedCount} suggested creators followed`} className={styles.progress}>
          <strong>{followedCount}<span>/3</span></strong>
          <small>suggested follows</small>
        </div>
      </header>

      {error && <p className={styles.error} role="alert">{error}</p>}

      <section aria-labelledby="suggested-creators-heading">
        <div className={styles.sectionHeading}>
          <div>
            <p className={styles.eyebrow}>PEOPLE TO KNOW</p>
            <h2 id="suggested-creators-heading">Suggested creators</h2>
          </div>
          <Link href="/discover">Open full Discover</Link>
        </div>
        <div className={styles.grid}>
          {creators.map((creator) => {
            const id = creatorId(creator);
            const username = creatorUsernameFor(creator);
            const isFollowing = Boolean(following[id]);
            return (
              <article className={styles.creator} key={id}>
                <Link
                  aria-label={`Open ${creator.title}'s creator profile`}
                  className={styles.identity}
                  href={username ? `/creator/${encodeURIComponent(username)}` : "/creators"}
                >
                  <CreatorAvatar displayName={creator.title} live={creator.live} size={58} username={username} />
                  <span>
                    <strong>{creator.title} <VerifiedBadge /></strong>
                    <small>{username ? `@${username}` : "FanBackstage creator"}</small>
                  </span>
                </Link>
                <p>{creator.description || "Public creator drops, Stories, and live moments."}</p>
                <button
                  aria-pressed={isFollowing}
                  disabled={working === id}
                  onClick={() => void toggleFollow(creator)}
                  type="button"
                >
                  {working === id ? "Saving…" : isFollowing ? "Following" : "Follow"}
                </button>
              </article>
            );
          })}
        </div>
        {!creators.length && !error && (
          <p className={styles.empty}>No eligible public creator suggestions are available right now.</p>
        )}
      </section>

      <footer className={styles.nextStep}>
        <div>
          <strong>{followedCount > 0 ? "Your feed has a starting point." : "Follow at least one creator to begin."}</strong>
          <span>You can follow or unfollow creators at any time.</span>
        </div>
        <Link href={followedCount > 0 ? "/feed" : "/discover"}>
          {followedCount > 0 ? "Open my feed" : "Keep exploring"}
        </Link>
      </footer>
    </div>
  );
}
