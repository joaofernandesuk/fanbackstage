"use client";

import Link from "next/link";
import { useRef } from "react";

import { isNavigationItemActive, publicNavigation } from "./navigation-model";
import { MenuIcon } from "./shell-icons";
import styles from "./app-header.module.css";

export function PublicNav({ pathname }: { pathname: string }) {
  const mobileMenu = useRef<HTMLDetailsElement>(null);
  const closeMobileMenu = () => mobileMenu.current?.removeAttribute("open");

  return (
    <>
      <div className={styles.publicDesktopShell}>
        <nav aria-label="Public navigation" className={styles.primaryNav}>
          {publicNavigation.map((item) => (
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
        <div className={styles.publicActions}>
          <Link className={styles.loginLink} href="/login">Log in</Link>
          <Link className={styles.joinButton} href="/register">Join</Link>
        </div>
      </div>

      <div className={styles.publicMobileShell}>
        <Link className={styles.mobileLoginLink} href="/login">Log in</Link>
        <Link className={styles.mobileJoinButton} href="/register">Join</Link>
        <details className={styles.mobilePublicMenu} ref={mobileMenu}>
          <summary aria-label="Open navigation menu" className={styles.iconControl}>
            <MenuIcon className={styles.actionIcon} />
          </summary>
          <nav aria-label="Mobile public navigation" className={styles.mobilePublicPanel}>
            {publicNavigation.map((item) => (
              <Link
                aria-current={isNavigationItemActive(pathname, item) ? "page" : undefined}
                className={styles.mobilePublicLink}
                href={item.href}
                key={item.href}
                onClick={closeMobileMenu}
              >
                {item.label}
              </Link>
            ))}
          </nav>
        </details>
      </div>
    </>
  );
}
