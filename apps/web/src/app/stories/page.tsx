import type { Metadata } from "next";

import { StoriesBrowser } from "../../components/stories-browser";
import styles from "./page.module.css";

export const metadata: Metadata = {
  title: "Stories",
  description: "Open immersive demo stories from eligible public FanBackstage creators.",
};

export default function StoriesPage() {
  return (
    <div className={styles.page}>
      <header>
        <p>Backstage stories</p>
        <h1>A little closer, one moment at a time.</h1>
        <span>Immersive creator stories built from public demo media. Creator eligibility always comes from FanBackstage discovery.</span>
      </header>
      <StoriesBrowser />
    </div>
  );
}
