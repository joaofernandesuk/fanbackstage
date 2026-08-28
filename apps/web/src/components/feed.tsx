"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { api, ApiError } from "../lib/api";
import { contentDeliveryUrl } from "../lib/content-api";
import type { ComplianceAccess } from "../lib/compliance-api";
import { formatMoney } from "../lib/public-api";
import { AdultAccessGate } from "./adult-access-gate";
import {
  AccessBadge,
  CreatorAvatar,
  EmptyState,
  Skeleton,
  useLoginGate,
} from "./consumer-ui";
import styles from "./social-surface.module.css";

type PostMedia = {
  derivative_id: string;
  delivery_path: string;
  media_type: "image" | "video";
  alt_text: string | null;
};
type ContentReference = {
  id: string;
  title: string;
  content_type?: string;
  locked: boolean;
  access_policy: string;
  price_amount_minor: number | null;
  price_currency: string | null;
};

type Post = ComplianceAccess & {
  id: string;
  creator_id: string;
  creator_username: string;
  creator_name: string;
  body: string | null;
  post_type: string;
  access_policy: string;
  locked: boolean;
  published_at: string | null;
  pinned_at: string | null;
  reaction_count: number;
  comment_count: number;
  viewer_reaction: string | null;
  reactions_enabled: boolean;
  comments_enabled: boolean;
  media: PostMedia[];
  content_reference: ContentReference | null;
};

type Page = ComplianceAccess & { items: Post[]; next_cursor: string | null };
type Comment = { id: string; user_id: string; body: string; created_at: string };

function relativeTime(value: string | null) {
  if (!value) return "Just now";
  const elapsed = Date.now() - new Date(value).getTime();
  const minutes = Math.max(1, Math.floor(elapsed / 60_000));
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  return days < 7 ? `${days}d` : new Intl.DateTimeFormat("en", { month: "short", day: "numeric" }).format(new Date(value));
}

function HeartIcon({ filled = false }: { filled?: boolean }) {
  return (
    <svg aria-hidden="true" className={styles.actionIcon} fill={filled ? "currentColor" : "none"} viewBox="0 0 24 24">
      <path d="M20.8 4.9a5.6 5.6 0 0 0-7.9 0L12 5.8l-.9-.9a5.6 5.6 0 0 0-7.9 7.9l.9.9L12 21l7.9-7.3.9-.9a5.6 5.6 0 0 0 0-7.9Z" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
    </svg>
  );
}

function CommentIcon() {
  return (
    <svg aria-hidden="true" className={styles.actionIcon} fill="none" viewBox="0 0 24 24">
      <path d="M20.5 11.5a8 8 0 0 1-8.4 8l-3.4 1.65.5-2.9A8 8 0 1 1 20.5 11.5Z" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
    </svg>
  );
}

function ShareIcon() {
  return (
    <svg aria-hidden="true" className={styles.actionIcon} fill="none" viewBox="0 0 24 24">
      <path d="M14 5h5v5M19 5l-8 8M19 14v4a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h4" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.8" />
    </svg>
  );
}

function PostCard({
  post,
  authenticated,
  onChanged,
  requireLogin,
}: {
  post: Post;
  authenticated: boolean;
  onChanged: () => Promise<void>;
  requireLogin: () => boolean;
}) {
  const [commentsOpen, setCommentsOpen] = useState(false);
  const [comments, setComments] = useState<Comment[]>([]);
  const [commentBody, setCommentBody] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const firstMedia = post.media[0];
  let mediaUrl: string | null = null;
  if (firstMedia) {
    try {
      mediaUrl = contentDeliveryUrl(firstMedia.delivery_path);
    } catch {
      mediaUrl = null;
    }
  }
  const mediaAlt = firstMedia?.alt_text || `${post.creator_name} creator post media`;
  const complianceBlocked = !post.compliance_allowed;

  async function react() {
    if (!authenticated) {
      requireLogin();
      return;
    }
    setWorking(true);
    setError("");
    try {
      await api(`/feed/posts/${post.id}/reaction`, {
        method: post.viewer_reaction ? "DELETE" : "PUT",
        body: post.viewer_reaction ? undefined : JSON.stringify({ reaction_type: "like" }),
      });
      await onChanged();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to update your reaction");
    } finally {
      setWorking(false);
    }
  }

  async function toggleComments() {
    const next = !commentsOpen;
    setCommentsOpen(next);
    if (next && !post.locked) {
      try {
        setComments(await api<Comment[]>(`/feed/posts/${post.id}/comments`));
      } catch {
        setComments([]);
      }
    }
  }

  async function comment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!authenticated) {
      requireLogin();
      return;
    }
    if (!commentBody.trim()) return;
    setWorking(true);
    try {
      await api(`/feed/posts/${post.id}/comments`, {
        method: "POST",
        body: JSON.stringify({ body: commentBody }),
      });
      setCommentBody("");
      setComments(await api<Comment[]>(`/feed/posts/${post.id}/comments`));
      await onChanged();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to add your comment");
    } finally {
      setWorking(false);
    }
  }

  async function report() {
    if (!authenticated) {
      requireLogin();
      return;
    }
    const reason = window.prompt("Tell us briefly why you are reporting this post.");
    if (!reason?.trim()) return;
    try {
      await api(`/feed/reports/post/${post.id}`, {
        method: "POST",
        body: JSON.stringify({ reason: reason.trim().slice(0, 80) }),
      });
      setError("Report received. Our safety team will review it.");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to submit this report");
    }
  }

  async function share() {
    const url = `${window.location.origin}/creator/${post.creator_username}`;
    if (navigator.share) {
      await navigator.share({ title: `${post.creator_name} on FanBackstage`, url }).catch(() => undefined);
    } else {
      await navigator.clipboard?.writeText(url);
      setError("Creator link copied.");
    }
  }

  return (
    <article className={styles.postCard}>
      <header className={styles.postHeader}>
        <Link aria-label={`Open ${post.creator_name}'s profile`} className={styles.creatorIdentity} href={`/creator/${post.creator_username}`}>
          <CreatorAvatar displayName={post.creator_name} size={48} username={post.creator_username} />
          <span>
            <strong>{post.creator_name}</strong>
            <small>@{post.creator_username} · {relativeTime(post.published_at)}</small>
          </span>
        </Link>
        <details className={styles.postMenu}>
          <summary aria-label={`More actions for ${post.creator_name}'s post`}>•••</summary>
          <div><button onClick={() => void report()} type="button">Report post</button></div>
        </details>
      </header>

      {complianceBlocked ? (
        <AdultAccessGate
          access={post}
          adultRestricted={post.adult_access_required}
          feature={post.adult_access_required ? "adult_media" : "platform_access"}
          onGranted={onChanged}
          title="this post"
        />
      ) : (mediaUrl || post.locked) ? (
        <div className={`${styles.postMedia} ${post.locked ? styles.lockedMedia : ""}`}>
          {mediaUrl && firstMedia?.media_type === "video" ? (
            <video aria-label={mediaAlt} controls playsInline preload="metadata" src={mediaUrl} />
          ) : mediaUrl ? (
            <img alt={mediaAlt} src={mediaUrl} />
          ) : null}
          <div className={styles.mediaBadges}>
            {post.pinned_at && <span className={styles.pinnedBadge}>Pinned</span>}
            <AccessBadge locked={post.locked} policy={post.access_policy} />
          </div>
          {post.locked && (
            <div className={styles.lockOverlay}>
              <span aria-hidden="true" className={styles.lockIcon}>◇</span>
              <strong>{post.access_policy === "ppv" ? "Premium post" : "Members-only post"}</strong>
              <span>{post.access_policy === "followers" ? "Follow this creator to view" : "Subscribe or unlock to continue"}</span>
              <Link href={`/creator/${post.creator_username}`}>View access options</Link>
            </div>
          )}
        </div>
      ) : null}

      <div className={styles.postBody}>
        {post.body && <p><Link href={`/creator/${post.creator_username}`}><strong>{post.creator_name}</strong></Link> {post.body}</p>}
        {post.content_reference && (
          <Link className={styles.contentReference} href={`/content/${encodeURIComponent(post.content_reference.id)}`}>
            <span>{post.content_reference.content_type || "premium content"}</span>
            <strong>{post.content_reference.title}</strong>
            {post.content_reference.price_amount_minor !== null && post.content_reference.price_currency && (
              <b>{formatMoney(post.content_reference.price_amount_minor, post.content_reference.price_currency)}</b>
            )}
          </Link>
        )}
      </div>

      <div className={styles.postActions}>
        <button aria-label={`${post.viewer_reaction ? "Unlike" : "Like"} post, ${post.reaction_count} likes`} className={post.viewer_reaction ? styles.actionActive : ""} disabled={working || !post.reactions_enabled} onClick={() => void react()} type="button">
          <HeartIcon filled={Boolean(post.viewer_reaction)} /><span>{post.reaction_count}</span>
        </button>
        <button aria-expanded={commentsOpen} aria-label={`${post.comment_count} comments`} disabled={post.locked || !post.comments_enabled} onClick={() => void toggleComments()} type="button">
          <CommentIcon /><span>{post.comment_count}</span>
        </button>
        <button aria-label="Share creator post" onClick={() => void share()} type="button"><ShareIcon /><span>Share</span></button>
      </div>

      {commentsOpen && (
        <section aria-label="Post comments" className={styles.comments}>
          {comments.length ? comments.slice(-4).map((entry) => (
            <div className={styles.comment} key={entry.id}>
              <span aria-hidden="true" className={styles.commentAvatar}>F</span>
              <p>{entry.body}<small>{relativeTime(entry.created_at)}</small></p>
            </div>
          )) : <p className={styles.commentHint}>Start the conversation.</p>}
          <form className={styles.commentForm} onSubmit={comment}>
            <label className="sr-only" htmlFor={`comment-${post.id}`}>Add a comment</label>
            <input id={`comment-${post.id}`} maxLength={2000} onChange={(event) => setCommentBody(event.target.value)} placeholder={authenticated ? "Add a comment…" : "Log in to join the conversation"} value={commentBody} />
            <button disabled={working || !commentBody.trim()} type="submit">Post</button>
          </form>
        </section>
      )}
      {error && <p className={styles.inlineMessage} role="status">{error}</p>}
    </article>
  );
}

export function Feed({ creatorId }: { creatorId?: string }) {
  const { authenticated, loading: authLoading, requireLogin } = useLoginGate();
  const [tab, setTab] = useState<"following" | "discover">("discover");
  const [items, setItems] = useState<Post[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [initialized, setInitialized] = useState(Boolean(creatorId));
  const [pageAccess, setPageAccess] = useState<Page | null>(null);

  useEffect(() => {
    if (creatorId || authLoading) return;
    setTab(authenticated ? "following" : "discover");
    setInitialized(true);
  }, [authLoading, authenticated, creatorId]);

  const path = useMemo(
    () => creatorId ? `/feed/creator/${creatorId}` : `/feed/${tab}`,
    [creatorId, tab],
  );

  const load = useCallback(async (next?: string | null, quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const page = await api<Page>(`${path}${next ? `?cursor=${encodeURIComponent(next)}` : ""}`);
      setPageAccess(page);
      setItems((old) => next ? [...old, ...page.items] : page.items);
      setCursor(page.next_cursor);
      setError("");
    } catch (caught) {
      const message = caught instanceof ApiError && caught.status === 401
        ? "Your following feed is ready after you log in."
        : caught instanceof ApiError ? caught.message : "Unable to load posts";
      setError(message);
      if (!next) setItems([]);
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    if (!initialized) return;
    setCursor(null);
    void load();
  }, [initialized, load]);

  return (
    <section aria-label={creatorId ? "Creator feed" : "Social feed"} className={styles.feed}>
      {!creatorId && (
        <div aria-label="Choose feed" className={styles.feedTabs} role="tablist">
          <button aria-selected={tab === "following"} onClick={() => authenticated ? setTab("following") : requireLogin()} role="tab" type="button">Following</button>
          <button aria-selected={tab === "discover"} onClick={() => setTab("discover")} role="tab" type="button">Discover</button>
        </div>
      )}

      {(loading || !initialized) && !items.length && <><Skeleton lines={2} /><Skeleton lines={2} /></>}
      {pageAccess && !pageAccess.compliance_allowed && (
        <AdultAccessGate
          access={pageAccess}
          adultRestricted={false}
          feature="platform_access"
          onGranted={() => load(null, true)}
          title="the social feed"
        />
      )}
      {error && !items.length && pageAccess?.compliance_allowed !== false && (
        <EmptyState
          action={!authenticated && <Link className={styles.primaryLink} href="/login?next=%2Ffeed">Log in to see your feed</Link>}
          body={error}
          title="Your social feed starts here"
        />
      )}
      {!loading && !error && !items.length && pageAccess?.compliance_allowed !== false && (
        <EmptyState
          action={<Link className={styles.primaryLink} href="/creators">Discover creators</Link>}
          body={tab === "following" ? "Follow a few creators and their latest posts will appear here." : "Fresh creator posts are on the way."}
          title={tab === "following" ? "Build your following feed" : "Nothing new right now"}
        />
      )}
      {items.map((post) => (
        <PostCard
          authenticated={authenticated}
          key={post.id}
          onChanged={() => load(null, true)}
          post={post}
          requireLogin={requireLogin}
        />
      ))}
      {cursor && <button className={styles.loadMore} disabled={loading} onClick={() => void load(cursor)} type="button">{loading ? "Loading…" : "Load more posts"}</button>}
    </section>
  );
}
