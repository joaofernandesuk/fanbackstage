"use client";

import Image from "next/image";
import Link from "next/link";
import { KeyboardEvent, MouseEvent, useEffect, useMemo, useRef, useState } from "react";

import { mediaForUsername, personaForUsername } from "../lib/demo-personas";
import { DiscoveryResult, creatorUsernameFor } from "../lib/public-api";
import { CreatorAvatar, EmptyState } from "./consumer-ui";
import styles from "./story-experience.module.css";

type StoryCreator = {
  result: DiscoveryResult;
  username: string;
  persona: NonNullable<ReturnType<typeof personaForUsername>>;
};

export function storyCreatorsFromDiscovery(creators: readonly DiscoveryResult[]): StoryCreator[] {
  return creators.flatMap((result) => {
    const username = creatorUsernameFor(result);
    const persona = personaForUsername(username);
    return username && persona ? [{ result, username, persona }] : [];
  });
}

export function StoryRail({ creators, limit }: { creators: readonly DiscoveryResult[]; limit?: number }) {
  const stories = useMemo(() => storyCreatorsFromDiscovery(creators).slice(0, limit), [creators, limit]);
  const [activeCreator, setActiveCreator] = useState<number | null>(null);
  const [activeSlide, setActiveSlide] = useState(0);
  const openerRef = useRef<HTMLButtonElement | null>(null);

  if (!stories.length) {
    return <EmptyState body="Public creator stories will appear here when discovery has eligible creators." title="No stories available" />;
  }

  function open(index: number, event: MouseEvent<HTMLButtonElement>) {
    openerRef.current = event.currentTarget;
    setActiveSlide(0);
    setActiveCreator(index);
  }

  function close() {
    setActiveCreator(null);
    requestAnimationFrame(() => openerRef.current?.focus());
  }

  return (
    <>
      <div aria-label="Creator stories" className={styles.rail} role="list">
        {stories.map((story, index) => (
          <div className={styles.storyItem} key={story.result.id} role="listitem">
            <button className={styles.storyButton} onClick={(event) => open(index, event)} type="button">
              <span className={styles.storyRing}>
                <CreatorAvatar displayName={story.result.title} size={66} username={story.username} />
              </span>
              <span>{story.result.title.split(" ")[0]}</span>
            </button>
          </div>
        ))}
      </div>
      {activeCreator !== null && (
        <StoryViewer
          activeCreator={activeCreator}
          activeSlide={activeSlide}
          onClose={close}
          onCreatorChange={(index) => { setActiveCreator(index); setActiveSlide(0); }}
          onSlideChange={setActiveSlide}
          stories={stories}
        />
      )}
    </>
  );
}

function StoryViewer({
  stories,
  activeCreator,
  activeSlide,
  onClose,
  onCreatorChange,
  onSlideChange,
}: {
  stories: StoryCreator[];
  activeCreator: number;
  activeSlide: number;
  onClose: () => void;
  onCreatorChange: (index: number) => void;
  onSlideChange: (index: number) => void;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const story = stories[activeCreator];
  const slide = story.persona.storySlides[activeSlide];
  const media = mediaForUsername(story.username);
  const source = media?.[slide.media];

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previous; };
  }, []);

  function previous() {
    if (activeSlide > 0) onSlideChange(activeSlide - 1);
    else if (activeCreator > 0) onCreatorChange(activeCreator - 1);
  }

  function next() {
    if (activeSlide < story.persona.storySlides.length - 1) onSlideChange(activeSlide + 1);
    else if (activeCreator < stories.length - 1) onCreatorChange(activeCreator + 1);
    else onClose();
  }

  function keyboard(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") onClose();
    if (event.key === "ArrowLeft") previous();
    if (event.key === "ArrowRight") next();
    if (event.key === "Home") { onCreatorChange(0); onSlideChange(0); }
    if (event.key === "End") onCreatorChange(stories.length - 1);
    if (event.key === "Tab") {
      const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("a[href], button:not(:disabled)"));
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  }

  return (
    <div aria-label={`${story.result.title} story`} aria-modal="true" className={styles.overlay} onKeyDown={keyboard} role="dialog">
      <div className={styles.viewer}>
        {source && <Image alt={`${story.result.title}: ${slide.title}`} fill priority sizes="100vw" src={source} />}
        <div className={styles.scrim} />
        <div aria-label={`Story ${activeSlide + 1} of ${story.persona.storySlides.length}`} className={styles.progress}>
          {story.persona.storySlides.map((_, index) => <span className={index <= activeSlide ? styles.complete : ""} key={index} />)}
        </div>
        <header className={styles.viewerHeader}>
          <CreatorAvatar displayName={story.result.title} size={42} username={story.username} />
          <div><strong>{story.result.title}</strong><span>@{story.username}</span></div>
          <button aria-label="Close story" className={styles.close} onClick={onClose} ref={closeRef} type="button">×</button>
        </header>
        <div className={styles.storyCopy}>
          <p>{slide.eyebrow}</p>
          <h2>{slide.title}</h2>
          <span>{slide.body}</span>
          <Link href={`/creator/${encodeURIComponent(story.username)}`}>View profile</Link>
        </div>
        <button aria-label="Previous story" className={`${styles.nav} ${styles.previous}`} disabled={activeCreator === 0 && activeSlide === 0} onClick={previous} type="button">‹</button>
        <button aria-label="Next story" className={`${styles.nav} ${styles.next}`} onClick={next} type="button">›</button>
      </div>
    </div>
  );
}
