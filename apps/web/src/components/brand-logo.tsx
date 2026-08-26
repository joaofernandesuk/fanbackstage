import Image from "next/image";
import Link from "next/link";

import styles from "./brand-logo.module.css";

export function BrandLogo() {
  return (
    <Link className={styles.link} href="/" aria-label="FanBackstage home">
      <Image
        alt=""
        className={styles.wordmark}
        height={156}
        priority
        sizes="188px"
        src="/brand/fanbackstage_wordmark_transparent.png"
        width={707}
      />
      <Image
        alt=""
        className={styles.symbol}
        height={303}
        priority
        sizes="46px"
        src="/brand/fanbackstage_symbol_transparent.png"
        width={446}
      />
    </Link>
  );
}
