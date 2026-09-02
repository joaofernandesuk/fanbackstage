"use client";

import Image from "next/image";
import Link from "next/link";
import { useEffect, useState } from "react";

import { api } from "../lib/api";
import {
  DiscoveryPage,
  DiscoveryResult,
  MarketplaceListing,
  discoverPath,
  discoverySearchPath,
} from "../lib/public-api";
import { ContentCard, CreatorCard, MarketplaceCard } from "./consumer-cards";
import { AdultAccessGate } from "./adult-access-gate";
import { EmptyState, SectionHeader, Skeleton } from "./consumer-ui";
import { StoryRailSource } from "./story-experience";
import styles from "./home-experience.module.css";

type HomeData = {
  discovery: DiscoveryResult[];
  creators: DiscoveryResult[];
  videos: DiscoveryResult[];
  listings: MarketplaceListing[];
};

const emptyData: HomeData = { discovery: [], creators: [], videos: [], listings: [] };
const noFailures: Record<keyof HomeData, boolean> = { discovery: false, creators: false, videos: false, listings: false };

export function HomeExperience() {
  const [data, setData] = useState<HomeData>(emptyData);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [failed, setFailed] = useState(noFailures);
  const [complianceGate, setComplianceGate] = useState<DiscoveryPage | null>(null);

  useEffect(() => {
    let active = true;
    Promise.allSettled([
      api<DiscoveryPage>(discoverPath(null, 50)),
      api<DiscoveryPage>(discoverySearchPath({ types: ["creator"], sort: "trending", limit: 50 })),
      api<DiscoveryPage>(discoverySearchPath({ types: ["video"], sort: "newest", limit: 12 })),
      api<MarketplaceListing[]>("/marketplace/listings"),
    ]).then(([discovery, creators, videos, listings]) => {
      if (!active) return;
      const accessDenied = [discovery, creators, videos].find(
        (result): result is PromiseFulfilledResult<DiscoveryPage> =>
          result.status === "fulfilled" && result.value.compliance_allowed === false,
      );
      if (accessDenied) {
        setComplianceGate(accessDenied.value);
        setFailed(noFailures);
        setError("");
        setLoading(false);
        return;
      }
      setComplianceGate(null);
      const next = {
        discovery: discovery.status === "fulfilled" ? discovery.value.items : [],
        creators: creators.status === "fulfilled" ? creators.value.items.filter((item) => item.entity_type === "creator") : [],
        videos: videos.status === "fulfilled" ? videos.value.items.filter((item) => item.entity_type === "video") : [],
        listings: listings.status === "fulfilled" ? listings.value : [],
      };
      setData(next);
      const failures = {
        discovery: discovery.status === "rejected",
        creators: creators.status === "rejected",
        videos: videos.status === "rejected",
        listings: listings.status === "rejected",
      };
      setFailed(failures);
      if (Object.values(failures).some(Boolean)) {
        setError(Object.values(failures).every(Boolean) ? "FanBackstage discovery is temporarily unavailable." : "Some FanBackstage collections could not be refreshed. Available sections are still shown.");
      }
      setLoading(false);
    });
    return () => { active = false; };
  }, []);

  const sponsored = data.discovery.filter((item) => item.sponsored);
  const featured = [...sponsored.filter((item) => item.entity_type === "creator"), ...data.creators]
    .filter((item, index, all) => all.findIndex((other) => other.id === item.id) === index)
    .slice(0, 4);
  const live = data.creators.filter((creator) => creator.live).slice(0, 4);
  const liveDirectory = live.length ? live : data.creators.slice(0, 4);
  const trending = data.discovery
    .filter((item) => ["post", "gallery", "video"].includes(item.entity_type))
    .slice(0, 4);
  const rising = data.creators.slice(4, 8).length ? data.creators.slice(4, 8) : data.creators.slice(0, 4);

  return (
    <div className={styles.home}>
      <section className={styles.hero}>
        <div className={styles.heroCopy}>
          <p className={styles.eyebrow}>Your all-access creator network</p>
          <h1>Get closer.<br /><span>Go backstage.</span></h1>
          <p>Discover public drops, live moments and premium creator worlds in one place.</p>
          <div className={styles.heroActions}>
            <Link className={styles.primary} href="/creators">Explore creators</Link>
            <Link className={styles.secondary} href="/register">Join free</Link>
          </div>
        </div>
        <div aria-hidden="true" className={styles.heroVisual}>
          <div className={styles.heroFrame}>
            <Image alt="" fill priority sizes="(max-width: 800px) 0px, 42vw" src="/demo/creators/luna-sparks/cover.jpg" />
          </div>
          <span className={styles.heroPulse}>New creators every week</span>
        </div>
      </section>

      {error && <p className={styles.error} role="status">{error}</p>}

      {complianceGate ? (
        <AdultAccessGate
          access={complianceGate}
          adultRestricted={false}
          feature="platform_access"
          onGranted={() => window.location.reload()}
          title="FanBackstage"
        />
      ) : (
        <>

      <HomeSection body="Creator worlds selected from public discovery." eyebrow="Discover" href="/creators" id="featured-heading" loading={loading} title="Featured creators">
        {featured.map((item) => <CreatorCard item={item} key={item.id} />)}
        {!loading && !featured.length && <EmptyState body={failed.creators ? "Creator discovery could not be refreshed." : "Eligible public creators will appear here."} title={failed.creators ? "Featured creators are unavailable" : "No featured creators yet"} />}
      </HomeSection>

      <HomeSection body={live.length ? "Only creators currently reported live by FanBackstage carry a LIVE badge." : "No one is broadcasting right now. Follow these creators to catch their next real show."} eyebrow="Happening now" href="/live" id="live-heading" loading={loading} title={live.length ? "Live now" : "Creators to watch live"}>
        {liveDirectory.map((item) => <CreatorCard item={item} key={item.id} />)}
        {!loading && !liveDirectory.length && <EmptyState body={failed.creators ? "Live creator discovery could not be refreshed." : "When a creator starts a public broadcast, it will appear here in real time."} title={failed.creators ? "Live creators are unavailable" : "The stage is quiet right now"} />}
      </HomeSection>

      <HomeSection body="Recent public and safely previewable drops." eyebrow="Community pulse" href="/discover" id="trending-heading" loading={loading} title="Trending now" variant="content">
        {trending.map((item) => <ContentCard item={item} key={`${item.entity_type}-${item.id}`} />)}
        {!loading && !trending.length && <EmptyState body={failed.discovery ? "Trending discovery could not be refreshed." : "Public community drops will appear here."} title={failed.discovery ? "Trending is unavailable" : "Nothing is trending yet"} />}
      </HomeSection>

      <HomeSection body="More public creators worth knowing." eyebrow="New energy" href="/creators?sort=newest" id="rising-heading" loading={loading} title="Rising creators">
        {rising.map((item) => <CreatorCard item={item} key={item.id} />)}
        {!loading && !rising.length && <EmptyState body={failed.creators ? "Creator discovery could not be refreshed." : "New eligible creators will appear here."} title={failed.creators ? "Rising creators are unavailable" : "No rising creators yet"} />}
      </HomeSection>

      <HomeSection body="Free, subscriber and premium video previews—access is always explicit." eyebrow="Watch" href="/videos" id="video-heading" loading={loading} title="Latest videos" variant="content">
        {data.videos.slice(0, 4).map((item) => <ContentCard item={item} key={item.id} />)}
        {!loading && !data.videos.length && <EmptyState body={failed.videos ? "Video discovery could not be refreshed." : "Published creator videos will appear here."} title={failed.videos ? "Videos are unavailable" : "No video drops yet"} />}
      </HomeSection>

      <section aria-labelledby="stories-heading" className={styles.storySection}>
        <SectionHeader eyebrow="Fresh today" href="/stories" id="stories-heading" linkLabel="Open stories" title="Stories" />
        <StoryRailSource emptyBody="New active creator stories will appear here when they are available to you." limit={10} />
      </section>

      <HomeSection body="Creator-owned collectibles and limited drops." eyebrow="Creator marketplace" href="/marketplace" id="market-heading" loading={loading} title="Backstage finds" variant="market">
        {data.listings.slice(0, 4).map((listing) => <MarketplaceCard key={listing.id} listing={listing} />)}
        {!loading && !data.listings.length && <EmptyState body={failed.listings ? "Marketplace listings could not be refreshed." : "Published listings will appear here after marketplace review."} title={failed.listings ? "Marketplace is unavailable" : "No marketplace finds yet"} />}
      </HomeSection>
        </>
      )}
    </div>
  );
}

function HomeSection({
  children,
  body,
  eyebrow,
  href,
  id,
  loading,
  title,
  variant = "creator",
}: {
  children: React.ReactNode;
  body: string;
  eyebrow: string;
  href: string;
  id: string;
  loading: boolean;
  title: string;
  variant?: "creator" | "content" | "market";
}) {
  return (
    <section aria-labelledby={id} className={styles.section}>
      <SectionHeader body={body} eyebrow={eyebrow} href={href} id={id} title={title} />
      <div className={`${styles.grid} ${styles[variant]}`}>
        {loading ? [0, 1, 2, 3].map((value) => <Skeleton key={value} />) : children}
      </div>
    </section>
  );
}
