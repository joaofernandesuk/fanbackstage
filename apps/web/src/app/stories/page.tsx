import type { Metadata } from "next";

import { StoriesBrowser } from "../../components/stories-browser";
import styles from "./page.module.css";

export const metadata: Metadata = {
  title: "Stories",
  description: "Open active creator Stories that FanBackstage authorizes for you.",
};

export default function StoriesPage() {
  return (
    <div className={styles.page}>
      <header>
        <p>Backstage stories</p>
        <h1>A little closer, one moment at a time.</h1>
        <span>Fresh photo and video moments from creators you can access right now. Every Story and media response is resolved by FanBackstage.</span>
      </header>
      <StoriesBrowser />
    </div>
  );
}
