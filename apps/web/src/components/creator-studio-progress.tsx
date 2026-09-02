"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { api } from "../lib/api";
import {
  creatorComplianceIsCurrent,
  type CreatorComplianceProjection,
} from "../lib/creator-compliance";
import { ComplianceStatusCard } from "./compliance-status-card";
import styles from "./creator-studio-progress.module.css";

type CreatorProfile = CreatorComplianceProjection & {
  username: string | null;
  display_name: string | null;
  bio: string | null;
  status: string;
  is_public: boolean;
};

type ManagedContent = {
  access_policy: string;
  content_type: string;
  preview_duration_seconds?: number | null;
  status: string;
};
type ManagedPost = { status: string };
type SubscriptionOption = { duration: string };
type LiveSettings = { private_sessions_enabled: boolean };

export type CreatorProgressSnapshot = {
  profile: CreatorProfile | null;
  content: ManagedContent[];
  posts: ManagedPost[];
  subscriptionOptions: SubscriptionOption[];
  liveSettings: LiveSettings | null;
};

export type CreatorProgressItem = {
  complete: boolean;
  description: string;
  href: string;
  title: string;
};

export function creatorProgressItems(snapshot: CreatorProgressSnapshot): CreatorProgressItem[] {
  const profile = snapshot.profile;
  return [
    {
      complete: Boolean(
        profile?.status === "approved" &&
        profile.is_public &&
        creatorComplianceIsCurrent(profile) &&
        profile.username &&
        profile.display_name &&
        profile.bio?.trim(),
      ),
      description: "Use your approved, currently verified public identity, display name, and bio.",
      href: "/creator-onboarding",
      title: "Complete your public profile",
    },
    {
      complete: snapshot.posts.some((post) => post.status === "published"),
      description: "Give followers something real to find in their feed.",
      href: "#posts",
      title: "Publish your first post",
    },
    {
      complete: snapshot.subscriptionOptions.length > 0,
      description: "Enable at least one duration with an authoritative price.",
      href: "#subscriptions",
      title: "Offer a subscription",
    },
    {
      complete: snapshot.content.some((item) =>
        item.content_type === "gallery" || item.content_type === "video"),
      description: "Create a Gallery or Video using the existing protected media flow.",
      href: "#media-content",
      title: "Create media content",
    },
    {
      complete: snapshot.content.some((item) =>
        item.status === "published" && item.access_policy === "ppv"),
      description: "Publish a priced PPV Gallery or Video through review before calling it ready to earn.",
      href: "#media-content",
      title: "Publish your first PPV release",
    },
    {
      complete: snapshot.content.some((item) =>
        item.content_type === "video" &&
        (item.preview_duration_seconds ?? 0) > 0),
      description: "Use the saved video preview interval that drives the protected preview renderer.",
      href: "#media-content",
      title: "Configure a video preview",
    },
    {
      complete: Boolean(snapshot.liveSettings?.private_sessions_enabled),
      description: "Turn on private sessions only when your pricing is ready.",
      href: "#live",
      title: "Configure private sessions",
    },
  ];
}

export function CreatorStudioProgress() {
  const [snapshot, setSnapshot] = useState<CreatorProgressSnapshot | null>(null);
  const [partial, setPartial] = useState(false);

  useEffect(() => {
    let active = true;

    async function load() {
      const [profileResult, contentResult, postsResult, liveResult] = await Promise.allSettled([
        api<CreatorProfile>("/creators/me"),
        api<ManagedContent[]>("/content/mine"),
        api<{ items: ManagedPost[] }>("/feed/mine?limit=50"),
        api<LiveSettings>("/live/settings"),
      ]);

      const profile = profileResult.status === "fulfilled" ? profileResult.value : null;
      const subscriptionResult = profile?.username
        ? await Promise.allSettled([
          api<SubscriptionOption[]>(
            `/creators/${encodeURIComponent(profile.username)}/subscription-options`,
          ),
        ]).then(([result]) => result)
        : null;

      if (!active) return;
      setSnapshot({
        profile,
        content: contentResult.status === "fulfilled" ? contentResult.value : [],
        posts: postsResult.status === "fulfilled" ? postsResult.value.items : [],
        subscriptionOptions: subscriptionResult?.status === "fulfilled"
          ? subscriptionResult.value
          : [],
        liveSettings: liveResult.status === "fulfilled" ? liveResult.value : null,
      });
      setPartial(
        [profileResult, contentResult, postsResult, liveResult]
          .some((result) => result.status === "rejected") ||
        subscriptionResult?.status === "rejected",
      );
    }

    void load();
    return () => { active = false; };
  }, []);

  const items = useMemo(
    () => snapshot ? creatorProgressItems(snapshot) : [],
    [snapshot],
  );
  const completed = items.filter((item) => item.complete).length;

  return (
    <>
      <ComplianceStatusCard creator />
      <section aria-labelledby="studio-progress-heading" className={styles.progressCard}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>GET STARTED</p>
          <h2 id="studio-progress-heading">Creator Studio progress</h2>
          <p>Each earning-readiness item reflects saved application state, not a browser-only checklist.</p>
        </div>
        <div aria-label={`${completed} of ${items.length || 7} steps complete`} className={styles.total}>
          <strong>{snapshot ? completed : "–"}</strong>
          <span>of {items.length || 7}</span>
        </div>
      </header>

      {snapshot ? (
        <>
          <progress aria-label="Creator Studio setup progress" max={items.length} value={completed} />
          <ol className={styles.steps}>
            {items.map((item) => (
              <li className={item.complete ? styles.complete : undefined} key={item.title}>
                <span aria-hidden="true" className={styles.marker}>{item.complete ? "✓" : ""}</span>
                <div>
                  <strong>{item.title}</strong>
                  <p>{item.description}</p>
                </div>
                <Link href={item.href}>{item.complete ? "Review" : "Set up"}</Link>
              </li>
            ))}
          </ol>
          <nav aria-label="Creator business destinations" className={styles.destinations}>
            <Link href="/creator-studio/stories">Create a Story</Link>
            <Link href="#marketplace-fulfilment">Marketplace</Link>
            <Link href="/creator-studio/analytics">Analytics</Link>
            <Link href="/notifications">Notifications</Link>
          </nav>
          {partial && (
            <p className={styles.notice} role="status">
              Some setup state could not be refreshed. Unavailable items remain incomplete until the next reload.
            </p>
          )}
        </>
      ) : (
        <p aria-busy="true" className={styles.loading}>Checking saved creator setup…</p>
      )}
      </section>
    </>
  );
}
