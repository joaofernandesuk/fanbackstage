import Link from "next/link";

import { LoginGate } from "../../components/consumer-ui";
import { LiveNow } from "../../components/live-now";
import { PrivateSessionRoom } from "../../components/private-session-room";
import styles from "../../components/social-surface.module.css";

export default function LivePage() {
  return (
    <div className={styles.pageShell}>
      <header className={styles.pageIntro}>
        <div>
          <p className="eyebrow">LIVE BACKSTAGE</p>
          <h1>See it as it happens</h1>
          <p>Real broadcasts carry a live badge from server state. When the stage is quiet, discover creators and follow them for their next show.</p>
        </div>
        <Link className={styles.secondaryLink} href="/creators?filter=live">Discover creators</Link>
      </header>
      <LiveNow />
      <LoginGate className={styles.secondaryLink} label="Log in to view your private-session queue" nextPath="/live">
        <PrivateSessionRoom />
      </LoginGate>
    </div>
  );
}
