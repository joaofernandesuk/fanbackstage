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
import styles from "./story-experience.module.css";

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
              <span>Replies and reactions aren’t available yet.</span>
              <StoryReport key={story.id} storyId={story.id} />
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

function StoryReport({ storyId }: { storyId: string }) {
  const [state, setState] = useState<"idle" | "confirm" | "pending" | "sent">("idle");
  const [error, setError] = useState("");
  const { authenticated, loading, requireLogin } = useLoginGate();

  async function submit() {
    setState("pending");
    setError("");
    try {
      await api<{ reported: boolean }>(storyReportPath(storyId), {
        method: "POST",
        body: JSON.stringify({ reason: "story_safety_concern" }),
      });
      setState("sent");
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 401) {
        const next = `${window.location.pathname}${window.location.search}`;
        window.location.assign(`/login?next=${encodeURIComponent(next)}`);
        return;
      }
      setError(caught instanceof ApiError ? caught.message : "Unable to send report");
      setState("confirm");
    }
  }

  if (state === "sent") return <span role="status">Report received.</span>;
  if (state === "confirm") {
    return (
      <span className={styles.reportConfirm}>
        <span>Send this Story to the safety team?</span>
        <button onClick={() => setState("idle")} type="button">Cancel</button>
        <button onClick={() => void submit()} type="button">Send report</button>
        {error && <span role="alert">{error}</span>}
      </span>
    );
  }
  if (state === "pending") return <span role="status">Sending report…</span>;
  if (!authenticated) {
    return (
      <button
        className={styles.reportButton}
        disabled={loading}
        onClick={() => requireLogin()}
        type="button"
      >{loading ? "Checking access…" : "Log in to report story"}</button>
    );
  }
  return <button className={styles.reportButton} onClick={() => setState("confirm")} type="button">Report story</button>;
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
          onError={() => setError(true)}
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
