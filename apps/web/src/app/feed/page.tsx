import Link from "next/link";

import { Feed } from "../../components/feed";
import styles from "../../components/social-surface.module.css";

export default function FeedPage() {
  return (
    <div className={styles.pageShell}>
      <header className={styles.pageIntro}>
        <div>
          <p className="eyebrow">CREATOR SOCIAL</p>
          <h1>Your backstage feed</h1>
          <p>New public drops, premium previews, live updates, and the creators you follow—all in one place.</p>
        </div>
        <Link className={styles.secondaryLink} href="/discover">Tune your feed</Link>
      </header>
      <div className={styles.feedLayout}>
        <Feed />
        <aside aria-label="Feed help and discovery" className={styles.feedAside}>
          <section className={styles.asideCard}>
            <h2>Make it yours</h2>
            <p>Follow creators to build a personal feed. Locked media remains protected until you have the right access.</p>
            <div className={styles.asideActions}>
              <Link className={styles.primaryLink} href="/creators">Find creators</Link>
              <Link className={styles.secondaryLink} href="/videos">Browse videos</Link>
            </div>
          </section>
          <section className={styles.asideCard}>
            <p className="eyebrow">LIVE NEXT</p>
            <h2>Catch the room, not the recap</h2>
            <p>See active rooms first and explore creators who regularly broadcast when the stage is quiet.</p>
            <div className={styles.asideActions}><Link className={styles.secondaryLink} href="/live">Explore live</Link></div>
          </section>
        </aside>
      </div>
    </div>
  );
}
