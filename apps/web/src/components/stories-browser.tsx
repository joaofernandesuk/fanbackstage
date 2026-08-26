"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";

import { ApiError, api } from "../lib/api";
import { mediaForUsername, personaForUsername } from "../lib/demo-personas";
import { DiscoveryPage, DiscoveryResult, creatorUsernameFor, discoverySearchPath } from "../lib/public-api";
import { EmptyState, SectionHeader, Skeleton } from "./consumer-ui";
import { StoryRail } from "./story-experience";
import styles from "./stories-browser.module.css";

export function StoriesBrowser() {
  const [creators, setCreators] = useState<DiscoveryResult[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    api<DiscoveryPage>(discoverySearchPath({ types: ["creator"], sort: "newest", limit: 50 }))
      .then((page) => { if (active) setCreators(page.items.filter((item) => item.entity_type === "creator")); })
      .catch((caught: unknown) => { if (active) setError(caught instanceof ApiError ? caught.message : "Unable to load stories"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const demoEligible = creators.filter((creator) => personaForUsername(creatorUsernameFor(creator)));

  if (loading) return <div className={styles.loading}>{[0, 1, 2, 3].map((value) => <Skeleton key={value} />)}</div>;
  if (error) return <EmptyState body={error} title="Stories are unavailable" />;
  if (!demoEligible.length) return <EmptyState body="Stories use demo presentation metadata only after an eligible creator is returned by public discovery." title="No public stories yet" />;

  return (
    <div className={styles.browser}>
      <section aria-labelledby="story-rail-title" className={styles.railSection}>
        <SectionHeader body="Select a creator, then use the arrow keys to move and Escape to close." eyebrow="Tap to open" id="story-rail-title" title="Today’s stories" />
        <StoryRail creators={demoEligible} />
      </section>
      <section aria-labelledby="story-creators-title">
        <SectionHeader body="Every story shown here belongs to a creator currently returned by public discovery." eyebrow="From the community" id="story-creators-title" title="More backstage moments" />
        <div className={styles.grid}>
          {demoEligible.map((creator) => {
            const username = creatorUsernameFor(creator)!;
            const media = mediaForUsername(username);
            const persona = personaForUsername(username)!;
            return (
              <article className={styles.editorialCard} key={creator.id}>
                {media && <Image alt="" fill sizes="(max-width: 700px) 86vw, 340px" src={media.portrait} />}
                <div className={styles.scrim} />
                <div className={styles.copy}>
                  <p>{persona.editorialLabel}</p>
                  <h2>{creator.title}</h2>
                  <span>@{username}</span>
                  <Link href={`/creator/${encodeURIComponent(username)}`}>View creator</Link>
                </div>
              </article>
            );
          })}
        </div>
      </section>
    </div>
  );
}
