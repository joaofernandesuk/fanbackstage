"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

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
import { ReportDialog, type ReportTarget } from "./report-dialog";
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
  media_delivery_path?: string;
  media_kind?: "playback" | "trailer";
  poster_delivery_path?: string;
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
  reaction_counts: Record<string, number>;
  comment_count: number;
  viewer_reaction: string | null;
  reactions_enabled: boolean;
  comments_enabled: boolean;
  media: PostMedia[];
  content_reference: ContentReference | null;
};

type Page = ComplianceAccess & { items: Post[]; next_cursor: string | null };
type Comment = {
  id: string;
  user_id: string;
  parent_id: string | null;
  body: string;
  created_at: string;
  reaction_count: number;
  reaction_counts: Record<string, number>;
  viewer_reaction: string | null;
};

type ReactionKind = "like" | "love" | "fire" | "wow";

type ReactionDetail = {
  reaction_type: ReactionKind;
  creator: { display_name: string; username: string } | null;
};

const REACTIONS: Array<{ kind: ReactionKind; label: string; symbol: string }> = [
  { kind: "like", label: "Like", symbol: "👍" },
  { kind: "love", label: "Love", symbol: "❤️" },
  { kind: "fire", label: "Fire", symbol: "🔥" },
  { kind: "wow", label: "Wow", symbol: "😮" },
];

function reactionOption(value: string | null) {
  return REACTIONS.find((reaction) => reaction.kind === value) ?? null;
}

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
  const [replyTarget, setReplyTarget] = useState<Comment | null>(null);
  const [working, setWorking] = useState(false);
  const [reactionPickerOpen, setReactionPickerOpen] = useState(false);
  const [commentReactionPickerId, setCommentReactionPickerId] = useState<string | null>(null);
  const [reactionBurst, setReactionBurst] = useState<ReactionKind | null>(null);
  const [reactionDetails, setReactionDetails] = useState<ReactionDetail[]>([]);
  const [reactionDetailsLoading, setReactionDetailsLoading] = useState(false);
  const [reactionDetailsError, setReactionDetailsError] = useState("");
  const [reportTarget, setReportTarget] = useState<ReportTarget | null>(null);
  const [mediaFailed, setMediaFailed] = useState(false);
  const [referenceVideoFailed, setReferenceVideoFailed] = useState(false);
  const [referencePosterFailed, setReferencePosterFailed] = useState(false);
  const [error, setError] = useState("");
  const reactionCloseTimer = useRef<number | null>(null);
  const commentReactionCloseTimer = useRef<number | null>(null);
  const reactionDetailsDialog = useRef<HTMLDialogElement>(null);
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
  const referenceVideoUrl = post.content_reference?.media_delivery_path
    ? contentDeliveryUrl(post.content_reference.media_delivery_path)
    : null;
  const referencePosterUrl = post.content_reference?.poster_delivery_path
    ? contentDeliveryUrl(post.content_reference.poster_delivery_path)
    : null;
  const complianceBlocked = !post.compliance_allowed;
  const activeReaction = reactionOption(post.viewer_reaction);
  const visibleReactionCounts = REACTIONS.filter(
    (reaction) => (post.reaction_counts?.[reaction.kind] ?? 0) > 0,
  );

  function keepReactionPickerOpen() {
    if (reactionCloseTimer.current) {
      window.clearTimeout(reactionCloseTimer.current);
      reactionCloseTimer.current = null;
    }
    setReactionPickerOpen(true);
  }

  function closeReactionPickerAfterPointerGrace() {
    if (reactionCloseTimer.current) window.clearTimeout(reactionCloseTimer.current);
    reactionCloseTimer.current = window.setTimeout(() => {
      setReactionPickerOpen(false);
      reactionCloseTimer.current = null;
    }, 220);
  }

  function keepCommentReactionPickerOpen(commentId: string) {
    if (commentReactionCloseTimer.current) {
      window.clearTimeout(commentReactionCloseTimer.current);
      commentReactionCloseTimer.current = null;
    }
    setCommentReactionPickerId(commentId);
  }

  function closeCommentReactionPickerAfterPointerGrace() {
    if (commentReactionCloseTimer.current) window.clearTimeout(commentReactionCloseTimer.current);
    commentReactionCloseTimer.current = window.setTimeout(() => {
      setCommentReactionPickerId(null);
      commentReactionCloseTimer.current = null;
    }, 220);
  }

  useEffect(
    () => () => {
      if (reactionCloseTimer.current) window.clearTimeout(reactionCloseTimer.current);
      if (commentReactionCloseTimer.current) {
        window.clearTimeout(commentReactionCloseTimer.current);
      }
    },
    [],
  );

  async function react(reactionType: ReactionKind) {
    if (!authenticated) {
      requireLogin();
      return;
    }
    setWorking(true);
    setError("");
    try {
      await api(`/feed/posts/${post.id}/reaction`, {
        method: post.viewer_reaction === reactionType ? "DELETE" : "PUT",
        body: post.viewer_reaction === reactionType ? undefined : JSON.stringify({ reaction_type: reactionType }),
      });
      if (post.viewer_reaction !== reactionType) {
        setReactionBurst(reactionType);
        window.setTimeout(() => setReactionBurst(null), 420);
      }
      setReactionPickerOpen(false);
      await onChanged();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to update your reaction");
    } finally {
      setWorking(false);
    }
  }

  async function openReactionDetails() {
    if (!post.reaction_count) return;
    setReactionDetailsLoading(true);
    setReactionDetailsError("");
    reactionDetailsDialog.current?.showModal();
    try {
      const result = await api<{ items: ReactionDetail[] }>(`/feed/posts/${post.id}/reactions`);
      setReactionDetails(result.items);
    } catch (caught) {
      setReactionDetailsError(
        caught instanceof ApiError ? caught.message : "Unable to load reactions right now",
      );
    } finally {
      setReactionDetailsLoading(false);
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
        body: JSON.stringify({
          body: commentBody,
          ...(replyTarget ? { parent_id: replyTarget.id } : {}),
        }),
      });
      setCommentBody("");
      setReplyTarget(null);
      setComments(await api<Comment[]>(`/feed/posts/${post.id}/comments`));
      await onChanged();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to add your comment");
    } finally {
      setWorking(false);
    }
  }

  async function reactToComment(comment: Comment, reactionType: ReactionKind) {
    if (!authenticated) {
      requireLogin();
      return;
    }
    setWorking(true);
    setError("");
    try {
      await api(`/feed/comments/${comment.id}/reaction`, {
        method: comment.viewer_reaction === reactionType ? "DELETE" : "PUT",
        body:
          comment.viewer_reaction === reactionType
            ? undefined
            : JSON.stringify({ reaction_type: reactionType }),
      });
      setCommentReactionPickerId(null);
      setComments(await api<Comment[]>(`/feed/posts/${post.id}/comments`));
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to update your reaction");
    } finally {
      setWorking(false);
    }
  }

  function openReport(target: ReportTarget) {
    if (!authenticated) {
      requireLogin();
      return;
    }
    setReportTarget(target);
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
          <div>
            <button
              onClick={() => openReport({ targetType: "post", targetId: post.id, label: "post" })}
              type="button"
            >
              Report post
            </button>
          </div>
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
          {mediaUrl && !mediaFailed && firstMedia?.media_type === "video" ? (
            <video
              aria-label={mediaAlt}
              controlsList="nodownload noremoteplayback"
              controls
              disableRemotePlayback
              onError={() => setMediaFailed(true)}
              onContextMenu={(event) => event.preventDefault()}
              playsInline
              preload="metadata"
              src={mediaUrl}
            />
          ) : mediaUrl && !mediaFailed ? (
            <img alt={mediaAlt} onError={() => setMediaFailed(true)} src={mediaUrl} />
          ) : !post.locked ? (
            <div className={styles.mediaUnavailable} role="status">
              Media is temporarily unavailable.
            </div>
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
          <div className={styles.contentReferenceCard}>
            {referenceVideoUrl && !referenceVideoFailed ? (
              <video
                aria-label={`${post.content_reference.title} ${post.content_reference.media_kind === "trailer" ? "preview" : "video"}`}
                className={styles.contentReferenceVideo}
                controls
                controlsList="nodownload noremoteplayback"
                disableRemotePlayback
                onError={() => setReferenceVideoFailed(true)}
                onContextMenu={(event) => event.preventDefault()}
                playsInline
                poster={referencePosterUrl || undefined}
                preload="metadata"
                src={referenceVideoUrl}
              />
            ) : referencePosterUrl && !referencePosterFailed ? (
              <img
                alt={`${post.content_reference.title} video preview`}
                className={styles.contentReferencePoster}
                onError={() => setReferencePosterFailed(true)}
                src={referencePosterUrl}
              />
            ) : null}
            <Link className={styles.contentReference} href={`/content/${encodeURIComponent(post.content_reference.id)}`}>
              <span>
                {post.content_reference.media_kind === "trailer" ? "Video preview" : post.content_reference.content_type || "premium content"}
              </span>
              <strong>{post.content_reference.title}</strong>
              {post.content_reference.price_amount_minor !== null && post.content_reference.price_currency && (
                <b>{formatMoney(post.content_reference.price_amount_minor, post.content_reference.price_currency)}</b>
              )}
            </Link>
          </div>
        )}
      </div>

      <div className={styles.postActions}>
        <div
          className={styles.reactionControl}
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget)) setReactionPickerOpen(false);
          }}
          onFocus={keepReactionPickerOpen}
          onMouseEnter={keepReactionPickerOpen}
          onMouseLeave={closeReactionPickerAfterPointerGrace}
        >
          <button
            aria-label={activeReaction ? `Remove ${activeReaction.label} reaction` : "Like post"}
            className={activeReaction ? styles.actionActive : ""}
            disabled={working || !post.reactions_enabled}
            onClick={() => void react("like")}
            type="button"
          >
            {activeReaction ? <span aria-hidden="true" className={styles.reactionGlyph}>{activeReaction.symbol}</span> : <HeartIcon />}
          </button>
          {post.reaction_count > 0 && (
            <button
              aria-label={`View ${post.reaction_count} reactions`}
              className={styles.reactionDetailsTrigger}
              onClick={() => void openReactionDetails()}
              type="button"
            >
            {visibleReactionCounts.length > 0 && (
              <span aria-hidden="true" className={styles.reactionSummary}>
                {visibleReactionCounts.slice(0, 3).map((reaction) => reaction.symbol)}
              </span>
            )}
            <span>{post.reaction_count}</span>
            </button>
          )}
          <div aria-label="Choose a reaction" className={`${styles.reactionPicker} ${reactionPickerOpen ? styles.reactionPickerOpen : ""}`} role="group">
            {REACTIONS.map((reaction) => (
              <button
                aria-label={`React ${reaction.label}`}
                className={reactionBurst === reaction.kind ? styles.reactionBurst : ""}
                disabled={working || !post.reactions_enabled}
                key={reaction.kind}
                onClick={() => void react(reaction.kind)}
                type="button"
              >
                <span aria-hidden="true">{reaction.symbol}</span>
              </button>
            ))}
          </div>
        </div>
        <button aria-expanded={commentsOpen} aria-label={`${post.comment_count} comments`} disabled={post.locked || !post.comments_enabled} onClick={() => void toggleComments()} type="button">
          <CommentIcon /><span>{post.comment_count}</span>
        </button>
        <button aria-label="Share creator post" onClick={() => void share()} type="button"><ShareIcon /><span>Share</span></button>
      </div>

      {commentsOpen && (
        <section aria-label="Post comments" className={styles.comments}>
          {comments.length ? comments.slice(-12).map((entry) => {
            const activeCommentReaction = reactionOption(entry.viewer_reaction);
            const commentReactionSummary = REACTIONS.filter(
              (reaction) => (entry.reaction_counts?.[reaction.kind] ?? 0) > 0,
            );
            return (
            <div className={`${styles.comment} ${entry.parent_id ? styles.commentReply : ""}`} key={entry.id}>
              <span aria-hidden="true" className={styles.commentAvatar}>F</span>
              <div className={styles.commentContent}>
                <p>{entry.body}<small>{relativeTime(entry.created_at)}</small></p>
                <div className={styles.commentActions}>
                  <div
                    className={styles.commentReactionControl}
                    onMouseEnter={() => keepCommentReactionPickerOpen(entry.id)}
                    onMouseLeave={closeCommentReactionPickerAfterPointerGrace}
                  >
                    <button
                      aria-label={activeCommentReaction ? `Remove ${activeCommentReaction.label} reaction` : "Like comment"}
                      className={activeCommentReaction ? styles.commentActionActive : ""}
                      disabled={working}
                      onClick={() => void reactToComment(entry, "like")}
                      type="button"
                    >
                      <span aria-hidden="true">{activeCommentReaction?.symbol || "♡"}</span>
                      {commentReactionSummary.length > 0 && (
                        <span aria-hidden="true">{commentReactionSummary.slice(0, 3).map((reaction) => reaction.symbol)}</span>
                      )}
                      {entry.reaction_count > 0 && <span>{entry.reaction_count}</span>}
                    </button>
                    {commentReactionPickerId === entry.id && (
                      <div aria-label="Choose a reaction" className={styles.commentReactionPicker} role="group">
                        {REACTIONS.map((reaction) => (
                          <button
                            aria-label={`React ${reaction.label} to comment`}
                            disabled={working}
                            key={reaction.kind}
                            onClick={() => void reactToComment(entry, reaction.kind)}
                            type="button"
                          >
                            {reaction.symbol}
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                  <button
                    className={styles.commentReplyButton}
                    onClick={() => setReplyTarget(entry)}
                    type="button"
                  >Reply</button>
                </div>
              </div>
              <details className={styles.commentMenu}>
                <summary aria-label="Comment options">•••</summary>
                <div>
                  <button
                    onClick={() => openReport({ targetType: "comment", targetId: entry.id, label: "comment" })}
                    type="button"
                  >
                    Report comment
                  </button>
                </div>
              </details>
            </div>
            );
          }) : <p className={styles.commentHint}>Start the conversation.</p>}
          <form className={styles.commentForm} onSubmit={comment}>
            {replyTarget && (
              <div className={styles.replyContext}>
                <span>Replying to a comment</span>
                <button onClick={() => setReplyTarget(null)} type="button">Cancel</button>
              </div>
            )}
            <label className="sr-only" htmlFor={`comment-${post.id}`}>{replyTarget ? "Write a reply" : "Add a comment"}</label>
            <input id={`comment-${post.id}`} maxLength={2000} onChange={(event) => setCommentBody(event.target.value)} placeholder={authenticated ? replyTarget ? "Write a reply…" : "Add a comment…" : "Log in to join the conversation"} value={commentBody} />
            <button disabled={working || !commentBody.trim()} type="submit">Post</button>
          </form>
        </section>
      )}
      <dialog
        aria-labelledby={`reaction-details-title-${post.id}`}
        className={styles.reactionDetailsDialog}
        onCancel={() => reactionDetailsDialog.current?.close()}
        onClick={(event) => {
          if (event.target === event.currentTarget) reactionDetailsDialog.current?.close();
        }}
        ref={reactionDetailsDialog}
      >
        <section className={styles.reactionDetailsPanel}>
          <div className={styles.reactionDetailsHeader}>
            <div>
              <span>REACTIONS</span>
              <h2 id={`reaction-details-title-${post.id}`}>Who reacted</h2>
            </div>
            <button aria-label="Close reactions" onClick={() => reactionDetailsDialog.current?.close()} type="button">×</button>
          </div>
          {reactionDetailsLoading ? (
            <p className={styles.reactionDetailsEmpty}>Loading reactions…</p>
          ) : reactionDetailsError ? (
            <p className={styles.reactionDetailsEmpty}>{reactionDetailsError}</p>
          ) : (
            <ul className={styles.reactionDetailsList}>
              {reactionDetails.map((reaction, index) => {
                const option = reactionOption(reaction.reaction_type);
                return (
                  <li key={`${reaction.creator?.username || "fan"}-${reaction.reaction_type}-${index}`}>
                    <span aria-hidden="true" className={styles.reactionDetailsEmoji}>{option?.symbol}</span>
                    {reaction.creator ? (
                      <Link href={`/creator/${reaction.creator.username}`}>
                        {reaction.creator.display_name}
                        <small>@{reaction.creator.username}</small>
                      </Link>
                    ) : (
                      <span className={styles.reactionDetailsFan}>Fan<small>{option?.label}</small></span>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
          <p className={styles.reactionDetailsPrivacy}>Fan identities stay private unless they have a public creator profile.</p>
        </section>
      </dialog>
      <ReportDialog
        onClose={() => setReportTarget(null)}
        onSubmitted={() => setError("Report received. Our safety team will review it.")}
        target={reportTarget}
      />
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
