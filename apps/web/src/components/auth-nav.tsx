import Link from "next/link";

import { authenticatedNavigation, isNavigationItemActive } from "./navigation-model";
import styles from "./app-header.module.css";

export function AuthNav({ pathname }: { pathname: string }) {
  return (
    <nav aria-label="Primary navigation" className={styles.authNav}>
      {authenticatedNavigation.map((item) => (
        <Link
          aria-current={isNavigationItemActive(pathname, item) ? "page" : undefined}
          className={styles.navLink}
          href={item.href}
          key={item.href}
        >
          {item.label}
        </Link>
      ))}
    </nav>
  );
}
