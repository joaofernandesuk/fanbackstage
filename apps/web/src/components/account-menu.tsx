"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef } from "react";

import {
  accountNavigation,
  type NavigationIdentity,
  type NavigationItem,
} from "./navigation-model";
import { ChevronDownIcon, LogOutIcon } from "./shell-icons";
import styles from "./app-header.module.css";

const groupLabels: Record<NonNullable<NavigationItem["group"]>, string> = {
  account: "Account",
  creator: "Creator",
  manager: "Agency",
  operations: "Operations",
};

function roleLabel(roles: string[]) {
  if (roles.includes("admin") || roles.includes("super_admin")) return "Administrator";
  if (roles.includes("moderator")) return "Moderator";
  if (roles.includes("manager")) return "Group manager";
  if (roles.includes("creator")) return "Creator";
  return "Fan";
}

function maskedEmail(email: string) {
  const [localPart] = email.split("@", 1);
  return `${localPart || "account"}@…`;
}

export function AccountMenu({
  displayName,
  identity,
  onLogout,
}: {
  displayName?: string | null;
  identity: NavigationIdentity;
  onLogout: () => Promise<void>;
}) {
  const menu = useRef<HTMLDetailsElement>(null);
  const items = useMemo(() => accountNavigation(identity), [identity]);
  const initial = (displayName || identity.email).trim().slice(0, 1).toUpperCase();

  useEffect(() => {
    function close(event: PointerEvent) {
      if (menu.current && !menu.current.contains(event.target as Node)) {
        menu.current.removeAttribute("open");
      }
    }
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        menu.current?.removeAttribute("open");
        menu.current?.querySelector("summary")?.focus();
      }
    }
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", escape);
    };
  }, []);

  const groups = (["account", "creator", "manager", "operations"] as const)
    .map((group) => ({ group, items: items.filter((item) => item.group === group) }))
    .filter((section) => section.items.length > 0);

  return (
    <details className={styles.accountMenu} ref={menu}>
      <summary aria-label="Open account menu" className={styles.accountSummary}>
        <span aria-hidden="true" className={styles.avatar}>{initial}</span>
        <ChevronDownIcon className={styles.accountChevron} />
      </summary>
      <div className={styles.accountPanel}>
        <div aria-label={`Signed in as ${identity.email}`} className={styles.accountIdentity}>
          <span className={styles.accountName}>{displayName || roleLabel(identity.roles)}</span>
          <span className={styles.accountEmail}>{maskedEmail(identity.email)}</span>
          <span className={styles.rolePill}>{roleLabel(identity.roles)}</span>
        </div>
        {groups.map((section) => (
          <div className={styles.accountGroup} key={section.group}>
            <span className={styles.accountGroupLabel}>{groupLabels[section.group]}</span>
            {section.items.map((item) => (
              <Link
                className={styles.accountLink}
                href={item.href}
                key={`${section.group}-${item.href}-${item.label}`}
                onClick={() => menu.current?.removeAttribute("open")}
              >
                {item.label}
              </Link>
            ))}
          </div>
        ))}
        <button className={styles.logoutButton} onClick={() => void onLogout()} type="button">
          <LogOutIcon className={styles.accountLinkIcon} />
          Log out
        </button>
      </div>
    </details>
  );
}
