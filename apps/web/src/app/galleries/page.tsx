import type { Metadata } from "next";

import { GalleryBrowser } from "../../components/gallery-browser";
import styles from "../videos/page.module.css";

export const metadata: Metadata = {
  title: "Galleries",
  description: "Explore ordered free and premium creator galleries on FanBackstage.",
};

export default function GalleriesPage() {
  return (
    <div className={styles.page}>
      <header>
        <p>Creator galleries</p>
        <h1>Step inside every frame.</h1>
        <span>Browse ordered free releases and authorised previews for subscriber and Premium / PPV collections.</span>
      </header>
      <GalleryBrowser />
    </div>
  );
}
