import type { Metadata } from "next";

import { CreatorDirectory } from "../../components/creator-directory";
import styles from "./page.module.css";

export const metadata: Metadata = {
  title: "Creators",
  description: "Explore public FanBackstage creators by category, language and location.",
};

export default async function CreatorsPage({ searchParams }: { searchParams: Promise<{ filter?: string; sort?: string }> }) {
  const query = await searchParams;
  const initialSort = query.filter === "live" ? "live" : query.sort === "newest" ? "newest" : "trending";
  return (
    <div className={styles.page}>
      <header>
        <p>Creator directory</p>
        <h1>Find your people.</h1>
        <span>Search public profiles and explore creators by category, language, location or who is live right now.</span>
      </header>
      <CreatorDirectory initialSort={initialSort} />
    </div>
  );
}
