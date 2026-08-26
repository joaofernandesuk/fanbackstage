import type { Metadata } from "next";

import { MarketplaceBrowser } from "../../components/marketplace-browser";
import styles from "./page.module.css";

export const metadata: Metadata = {
  title: "Marketplace",
  description: "Shop reviewed, creator-owned items from the FanBackstage community.",
};

export default function MarketplacePage() {
  return (
    <div className={styles.page}>
      <header>
        <p>Creator marketplace</p>
        <h1>Own a piece of backstage.</h1>
        <span>Discover creator-owned collectibles, wardrobe, signed pieces and limited community drops.</span>
      </header>
      <MarketplaceBrowser />
    </div>
  );
}
