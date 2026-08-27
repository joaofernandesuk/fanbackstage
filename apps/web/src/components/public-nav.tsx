"use client";

import Link from "next/link";
import { useRef } from "react";

import {
  authEntryPath,
  AuthMode,
  DEFAULT_REGISTRATION_DESTINATION,
} from "../lib/auth-ui";
import { useAuthExperience } from "./auth-experience";
import { isNavigationItemActive, publicNavigation } from "./navigation-model";
import { MenuIcon } from "./shell-icons";
import styles from "./app-header.module.css";

export function PublicNav({ pathname }: { pathname: string }) {
  const mobileMenu = useRef<HTMLDetailsElement>(null);
  const { openAuth } = useAuthExperience();
  const closeMobileMenu = () => mobileMenu.current?.removeAttribute("open");

  function showAuth(event: { preventDefault: () => void }, mode: AuthMode) {
    event.preventDefault();
    closeMobileMenu();
    openAuth(mode, mode === "register" ? DEFAULT_REGISTRATION_DESTINATION : undefined);
  }

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
          <Link aria-haspopup="dialog" className={styles.loginLink} href={authEntryPath("login", pathname)} onNavigate={(event) => showAuth(event, "login")}>Log in</Link>
          <Link aria-haspopup="dialog" className={styles.joinButton} href={authEntryPath("register")} onNavigate={(event) => showAuth(event, "register")}>Join</Link>
        </div>
      </div>

      <div className={styles.publicMobileShell}>
        <Link aria-haspopup="dialog" className={styles.mobileLoginLink} href={authEntryPath("login", pathname)} onNavigate={(event) => showAuth(event, "login")}>Log in</Link>
        <Link aria-haspopup="dialog" className={styles.mobileJoinButton} href={authEntryPath("register")} onNavigate={(event) => showAuth(event, "register")}>Join</Link>
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
