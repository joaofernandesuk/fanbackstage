"use client";

import Link from "next/link";
import {
  KeyboardEvent,
  MouseEvent,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { ApiError, api } from "../lib/api";
import {
  PublicStory,
  StoryCreatorGroup,
  StoryRailPage,
  groupStoriesByCreator,
  storyAgeLabel,
  storyDetailPath,
  storyMediaUrl,
  storyProfilePath,
  storyRailPath,
  storyReportPath,
} from "../lib/stories-api";
import { AdultAccessGate } from "./adult-access-gate";
import { AccessBadge, CreatorAvatar, EmptyState, VerifiedBadge, useLoginGate } from "./consumer-ui";
import { ReportDialog, type ReportTarget } from "./report-dialog";
import styles from "./story-experience.module.css";

type StoryReactionKind = "like" | "love" | "fire" | "wow";

const STORY_REACTIONS: Array<{ kind: StoryReactionKind; label: string; symbol: string }> = [
  { kind: "like", label: "Like", symbol: "❤️" },
  { kind: "love", label: "Love", symbol: "😍" },
  { kind: "fire", label: "Fire", symbol: "🔥" },
  { kind: "wow", label: "Wow", symbol: "😮" },
];

export function StoryRailSource({
  limit = 12,
  emptyBody = "Active creator stories will appear here when they are available to you.",
  creatorUsername,
}: {
  limit?: number;
  emptyBody?: string;
  creatorUsername?: string;
}) {
  const [stories, setStories] = useState<PublicStory[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [access, setAccess] = useState<StoryRailPage | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setStories([]);
    api<StoryRailPage>(storyRailPath({ limit: 50, creatorUsername }))
      .then((page) => {
        if (active) {
          setStories(page.items);
          setAccess(page);
        }
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(caught instanceof ApiError ? caught.message : "Unable to load stories");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [creatorUsername, limit]);

  if (loading) {
    return (
      <div aria-busy="true" aria-label="Loading creator stories" className={styles.railSkeleton} role="status">
        {Array.from({ length: Math.min(limit, 7) }, (_, index) => <span key={index} />)}
      </div>
    );
  }
  if (error) return <EmptyState body={error} title="Stories are unavailable" />;
  if (access && !access.compliance_allowed) {
    return (
      <AdultAccessGate
        access={access}
        adultRestricted={false}
        feature="platform_access"
        onGranted={async () => {
          const page = await api<StoryRailPage>(storyRailPath({ limit: 50, creatorUsername }));
          setStories(page.items);
          setAccess(page);
        }}
        title="Stories"
      />
    );
  }
  return <StoryRail emptyBody={emptyBody} limit={creatorUsername ? 1 : limit} stories={stories} />;
}

export function StoryRail({
  stories,
  limit,
  emptyBody = "Active creator stories will appear here when they are available to you.",
}: {
  stories: readonly PublicStory[];
  limit?: number;
  emptyBody?: string;
}) {
  const [now, setNow] = useState(() => Date.now());
  const groups = useMemo(
    () => groupStoriesByCreator(stories, now).slice(0, limit),
    [stories, limit, now],
  );
  const [activeCreator, setActiveCreator] = useState<number | null>(null);
  const [activeStory, setActiveStory] = useState(0);
  const openerRef = useRef<HTMLButtonElement | null>(null);
  const activeCreatorIdRef = useRef<string | null>(null);

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

  const resolvedActiveCreator = activeCreator === null
    ? -1
    : groups.findIndex((group) => group.creator.id === activeCreatorIdRef.current);

  useEffect(() => {
    if (activeCreator !== null && resolvedActiveCreator < 0) {
      activeCreatorIdRef.current = null;
      setActiveCreator(null);
      requestAnimationFrame(() => openerRef.current?.focus());
    }
  }, [activeCreator, resolvedActiveCreator]);

  if (!groups.length) {
    return <EmptyState body={emptyBody} title="No active stories" />;
  }

  function open(index: number, event: MouseEvent<HTMLButtonElement>) {
    openerRef.current = event.currentTarget;
    activeCreatorIdRef.current = groups[index].creator.id;
    setActiveStory(0);
    setActiveCreator(index);
  }

  function close() {
    activeCreatorIdRef.current = null;
    setActiveCreator(null);
    requestAnimationFrame(() => openerRef.current?.focus());
  }

  return (
    <>
      <div aria-label="Creator stories" className={styles.rail} role="list">
        {groups.map((group, index) => (
          <div className={styles.storyItem} key={group.creator.id} role="listitem">
            <button
              aria-label={`Open ${group.creator.display_name}'s ${group.stories.length === 1 ? "story" : `${group.stories.length} stories`}`}
              className={styles.storyButton}
              onClick={(event) => open(index, event)}
              type="button"
            >
              <span className={styles.storyRing}>
                <CreatorAvatar
                  displayName={group.creator.display_name}
                  size={66}
                  username={group.creator.username}
                />
                {group.stories.length > 1 && <span aria-hidden="true" className={styles.storyCount}>{group.stories.length}</span>}
              </span>
              <span>{group.creator.display_name.split(" ")[0]}</span>
            </button>
          </div>
        ))}
      </div>
      {resolvedActiveCreator >= 0 && (
        <StoryViewer
          activeCreator={resolvedActiveCreator}
          activeStory={Math.min(activeStory, groups[resolvedActiveCreator].stories.length - 1)}
          groups={groups}
          onClose={close}
          onCreatorChange={(index, storyIndex = 0) => {
            activeCreatorIdRef.current = groups[index].creator.id;
            setActiveCreator(index);
            setActiveStory(storyIndex);
          }}
          onStoryChange={setActiveStory}
        />
      )}
    </>
  );
}

function StoryViewer({
  groups,
  activeCreator,
  activeStory,
  onClose,
  onCreatorChange,
  onStoryChange,
}: {
  groups: StoryCreatorGroup[];
  activeCreator: number;
  activeStory: number;
  onClose: () => void;
  onCreatorChange: (index: number, storyIndex?: number) => void;
  onStoryChange: (index: number) => void;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const group = groups[activeCreator];
  const story = group.stories[activeStory];
  const isFirst = activeCreator === 0 && activeStory === 0;
  const isLast = activeCreator === groups.length - 1 && activeStory === group.stories.length - 1;

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
    closeRef.current?.focus();
  }, []);

  function previous() {
    if (activeStory > 0) {
      onStoryChange(activeStory - 1);
      return;
    }
    if (activeCreator > 0) {
      const previousGroup = groups[activeCreator - 1];
      onCreatorChange(activeCreator - 1, previousGroup.stories.length - 1);
    }
  }

  function next() {
    if (activeStory < group.stories.length - 1) {
      onStoryChange(activeStory + 1);
      return;
    }
    if (activeCreator < groups.length - 1) {
      onCreatorChange(activeCreator + 1);
      return;
    }
    dialogRef.current?.close();
  }

  function keyboard(event: KeyboardEvent<HTMLDialogElement>) {
    if (event.target instanceof HTMLVideoElement) return;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      previous();
    } else if (event.key === "ArrowRight") {
      event.preventDefault();
      next();
    } else if (event.key === "Home") {
      event.preventDefault();
      onCreatorChange(0);
    } else if (event.key === "End") {
      event.preventDefault();
      const finalCreator = groups.length - 1;
      onCreatorChange(finalCreator, groups[finalCreator].stories.length - 1);
    }
  }

  function backdrop(event: MouseEvent<HTMLDialogElement>) {
    if (event.target === event.currentTarget) dialogRef.current?.close();
  }

  return (
    <dialog
      aria-label={`${group.creator.display_name} story`}
      className={styles.overlay}
      onCancel={(event) => {
        event.preventDefault();
        dialogRef.current?.close();
      }}
      onClick={backdrop}
      onClose={onClose}
      onKeyDown={keyboard}
      ref={dialogRef}
    >
      <div className={styles.viewer}>
        <StoryMedia key={story.id} story={story} />
        <div aria-hidden="true" className={styles.scrim} />
        <div
          aria-label={`Story ${activeStory + 1} of ${group.stories.length}`}
          className={styles.progress}
          role="status"
        >
          {group.stories.map((item, index) => (
            <span
              aria-current={index === activeStory ? "step" : undefined}
              className={index < activeStory ? styles.complete : index === activeStory ? styles.current : ""}
              key={item.id}
            />
          ))}
        </div>
        <header className={styles.viewerHeader}>
          <Link className={styles.creatorIdentity} href={storyProfilePath(group.creator.username)}>
            <CreatorAvatar
              displayName={group.creator.display_name}
              size={42}
              username={group.creator.username}
            />
            <span>
              <strong>{group.creator.display_name} {group.creator.verified && <VerifiedBadge />}</strong>
              <small>@{group.creator.username} · {storyAgeLabel(story.published_at)}</small>
            </span>
          </Link>
          <AccessBadge policy={story.access_policy} />
          <StoryMenu storyId={story.id} />
          <button
            aria-label="Close story"
            className={styles.close}
            onClick={() => dialogRef.current?.close()}
            ref={closeRef}
            type="button"
          >×</button>
        </header>
        <div className={styles.storyCopy}>
          {story.caption && <p>{story.caption}</p>}
          <div className={styles.storyActions}>
            <Link href={storyProfilePath(group.creator.username)}>View profile</Link>
            <div className={styles.storyActionMeta}>
              <StoryReaction key={story.id} story={story} />
            </div>
          </div>
        </div>
        <button
          aria-label="Previous story"
          className={`${styles.nav} ${styles.previous}`}
          disabled={isFirst}
          onClick={previous}
          type="button"
        >‹</button>
        <button
          aria-label="Next story"
          className={`${styles.nav} ${styles.next}`}
          onClick={next}
          type="button"
        >{isLast ? "×" : "›"}</button>
      </div>
    </dialog>
  );
}

function StoryMenu({ storyId }: { storyId: string }) {
  const [reportTarget, setReportTarget] = useState<ReportTarget | null>(null);
  const [error, setError] = useState("");
  const { authenticated, loading, requireLogin } = useLoginGate();

  function openReport() {
    if (!authenticated) {
      requireLogin();
      return;
    }
    setError("");
    setReportTarget({ targetType: "story", targetId: storyId, label: "story" });
  }

  return (
    <>
      <details className={styles.storyMenu}>
        <summary aria-label="Story options">•••</summary>
        <div>
          <button disabled={loading} onClick={openReport} type="button">Report story</button>
        </div>
      </details>
      <ReportDialog
        onClose={() => setReportTarget(null)}
        onSubmitted={() => setError("Report received. Our safety team will review it.")}
        submitPath={storyReportPath(storyId)}
        target={reportTarget}
      />
      {error && <span className={styles.storyMenuNotice} role="status">{error}</span>}
    </>
  );
}

function StoryReaction({ story }: { story: PublicStory }) {
  const { authenticated, loading, requireLogin } = useLoginGate();
  const [reaction, setReaction] = useState<StoryReactionKind | null>(
    story.viewer_reaction as StoryReactionKind | null,
  );
  const [counts, setCounts] = useState<Record<string, number>>(story.reaction_counts ?? {});
  const [pickerOpen, setPickerOpen] = useState(false);
  const [burst, setBurst] = useState<StoryReactionKind | null>(null);
  const [working, setWorking] = useState(false);
  const closeTimer = useRef<number | null>(null);
  const active = STORY_REACTIONS.find((item) => item.kind === reaction) ?? null;

  useEffect(() => {
    setReaction(story.viewer_reaction as StoryReactionKind | null);
    setCounts(story.reaction_counts ?? {});
  }, [story]);

  useEffect(() => () => {
    if (closeTimer.current) window.clearTimeout(closeTimer.current);
  }, []);

  function keepPickerOpen() {
    if (closeTimer.current) {
      window.clearTimeout(closeTimer.current);
      closeTimer.current = null;
    }
    setPickerOpen(true);
  }

  function closePickerAfterGrace() {
    if (closeTimer.current) window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(() => {
      setPickerOpen(false);
      closeTimer.current = null;
    }, 220);
  }

  async function react(kind: StoryReactionKind) {
    if (!authenticated) {
      requireLogin();
      return;
    }
    setWorking(true);
    try {
      const removing = reaction === kind;
      await api(`/stories/${story.id}/reaction`, {
        method: removing ? "DELETE" : "PUT",
        body: removing ? undefined : JSON.stringify({ reaction_type: kind }),
      });
      setCounts((current) => {
        const next = { ...current };
        if (reaction) next[reaction] = Math.max(0, (next[reaction] ?? 0) - 1);
        if (!removing) next[kind] = (next[kind] ?? 0) + 1;
        return next;
      });
      setReaction(removing ? null : kind);
      if (!removing) {
        setBurst(kind);
        window.setTimeout(() => setBurst(null), 520);
      }
      setPickerOpen(false);
    } finally {
      setWorking(false);
    }
  }

  const total = Object.values(counts).reduce((sum, count) => sum + count, 0);
  return (
    <div
      className={styles.storyReactionControl}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setPickerOpen(false);
      }}
      onFocus={keepPickerOpen}
      onMouseEnter={keepPickerOpen}
      onMouseLeave={closePickerAfterGrace}
    >
      <button
        aria-label={active ? `Remove ${active.label} reaction` : "React to story"}
        className={active ? styles.storyReactionActive : ""}
        disabled={loading || working}
        onClick={() => void react("like")}
        type="button"
      >
        <span aria-hidden="true">{active?.symbol ?? "♡"}</span>
        {total > 0 && <small>{total}</small>}
      </button>
      <div aria-label="Choose a reaction" className={`${styles.storyReactionPicker} ${pickerOpen ? styles.storyReactionPickerOpen : ""}`} role="group">
        {STORY_REACTIONS.map((item) => (
          <button
            aria-label={`React ${item.label} to story`}
            className={burst === item.kind ? styles.storyReactionBurst : ""}
            disabled={loading || working}
            key={item.kind}
            onClick={() => void react(item.kind)}
            type="button"
          >{item.symbol}</button>
        ))}
      </div>
    </div>
  );
}

function StoryMedia({ story }: { story: PublicStory }) {
  const [resolvedStory, setResolvedStory] = useState(story);
  const media = storyMediaUrl(resolvedStory);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    setResolvedStory(story);
    setLoaded(false);
    setError(false);
  }, [story]);

  if (!resolvedStory.compliance_allowed) {
    return (
      <AdultAccessGate
        access={resolvedStory}
        adultRestricted={resolvedStory.adult_access_required}
        feature={resolvedStory.adult_access_required ? "adult_media" : "platform_access"}
        onGranted={async () => setResolvedStory(await api<PublicStory>(storyDetailPath(story.id)))}
        title="this Story"
      />
    );
  }

  if (!media || error) {
    return <div className={styles.mediaState} role="status"><strong>Story media unavailable</strong><span>You can move to another story or try again later.</span></div>;
  }
  return (
    <>
      {!loaded && <div aria-label="Loading story media" aria-busy="true" className={styles.mediaLoading} role="status" />}
      {resolvedStory.media_type === "video" ? (
        <video
          aria-label={resolvedStory.alt_text ?? `${resolvedStory.creator.display_name} story video`}
          className={loaded ? "" : styles.mediaHidden}
          controls
          controlsList="nodownload noremoteplayback"
          disableRemotePlayback
          onError={() => setError(true)}
          onContextMenu={(event) => event.preventDefault()}
          onLoadedData={() => setLoaded(true)}
          playsInline
          preload="metadata"
          src={media}
        />
      ) : (
        <img
          alt={resolvedStory.alt_text ?? `${resolvedStory.creator.display_name} story`}
          className={loaded ? "" : styles.mediaHidden}
          onError={() => setError(true)}
          onLoad={() => setLoaded(true)}
          src={media}
        />
      )}
    </>
  );
}
