import Link from "next/link";

import {
  isNavigationItemActive,
  mobileNavigation,
  type NavigationIdentity,
} from "./navigation-model";
import { NavigationItemIcon } from "./shell-icons";
import styles from "./app-header.module.css";

export function MobileBottomNav({
  identity,
  messageUnread,
  pathname,
}: {
  identity: NavigationIdentity;
  messageUnread: number;
  pathname: string;
}) {
  return (
    <nav aria-label="Mobile navigation" className={styles.mobileBottomNav}>
      <div className={styles.mobileBottomInner}>
        {mobileNavigation(identity).map((item) => {
          const active = isNavigationItemActive(pathname, item);
          return (
            <Link
              aria-current={active ? "page" : undefined}
              className={styles.mobileBottomLink}
              href={item.href}
              key={`${item.label}-${item.href}`}
            >
              <span className={styles.mobileIconWrap}>
                <NavigationItemIcon className={styles.mobileBottomIcon} icon={item.icon} />
                {item.icon === "messages" && messageUnread > 0 && (
                  <span className={styles.mobileUnreadDot}>
                    <span className="sr-only">{messageUnread} unread messages</span>
                  </span>
                )}
              </span>
              <span>{item.label}</span>
            </Link>
          );
        })}
      </div>
    </nav>
  );
}
