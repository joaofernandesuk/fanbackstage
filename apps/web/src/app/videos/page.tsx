import type { Metadata } from "next";

import { VideoBrowser } from "../../components/video-browser";
import styles from "./page.module.css";

export const metadata: Metadata = {
  title: "Videos",
  description: "Watch free and safely previewable premium creator videos on FanBackstage.",
};

export default function VideosPage() {
  return (
    <div className={styles.page}>
      <header>
        <p>Creator cinema</p>
        <h1>Watch what’s happening backstage.</h1>
        <span>Browse free drops, subscriber releases and Premium / PPV previews. Protected originals stay protected.</span>
      </header>
      <VideoBrowser />
    </div>
  );
}
