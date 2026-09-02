"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError, api } from "../lib/api";
import { useLoginGate } from "./consumer-ui";
import styles from "./social-surface.module.css";

type CreatorStatus = "draft" | "pending_verification" | "pending_review" | "approved" | "rejected" | "suspended";
type CreatorSelf = { status: CreatorStatus; creator_compliance?: { public_allowed: boolean } };

export function FeedStoryComposerPrompt() {
  const { authenticated, loading } = useLoginGate();
  const [profile, setProfile] = useState<CreatorSelf | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!authenticated) { setProfile(null); setLoaded(true); return; }
    let active = true;
    void api<CreatorSelf>("/creators/me")
      .then((next) => { if (active) setProfile(next); })
      .catch((caught) => { if (active && !(caught instanceof ApiError && caught.status === 404)) setProfile(null); })
      .finally(() => { if (active) setLoaded(true); });
    return () => { active = false; };
  }, [authenticated]);

  if (loading || !loaded || !authenticated) return null;
  if (profile?.status === "approved" && profile.creator_compliance?.public_allowed) return <section className={styles.storyComposerPrompt}><div><p className="eyebrow">YOUR STORY</p><h2>Share a moment with your fans</h2><p>Capture a photo, add local face effects, text and a filter, then publish a protected 24-hour Story.</p></div><Link className={styles.primaryLink} href="/creator-studio/stories">Create a Story</Link></section>;
  if (profile?.status === "pending_verification") return <section className={styles.storyComposerPrompt}><div><p className="eyebrow">CREATE STORIES</p><h2>Complete your creator identity check first</h2><p>Stories are creator publishing. Finish the identity check, then an authorised admin can review your application.</p></div><Link className={styles.secondaryLink} href="/creator-onboarding">Continue creator application</Link></section>;
  if (!profile) return <section className={styles.storyComposerPrompt}><div><p className="eyebrow">CREATE STORIES</p><h2>Want to share your own Story?</h2><p>Fans can watch Stories here. Apply as a creator to publish your own protected photo and video Stories.</p></div><Link className={styles.secondaryLink} href="/creator-onboarding">Become a creator</Link></section>;
  return <section className={styles.storyComposerPrompt}><div><p className="eyebrow">CREATE STORIES</p><h2>Your creator application is being reviewed</h2><p>Publishing becomes available after an authorised decision and the current creator policy is satisfied.</p></div><Link className={styles.secondaryLink} href="/creator-onboarding">View application</Link></section>;
}
