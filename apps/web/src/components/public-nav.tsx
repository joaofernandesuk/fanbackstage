"use client";

import Link from "next/link";
import { MouseEvent, useRef, useState } from "react";

import { AuthMode } from "../lib/auth-ui";
import { AuthDialog } from "./auth-dialog";
import { isNavigationItemActive, publicNavigation } from "./navigation-model";
import { MenuIcon } from "./shell-icons";
import styles from "./app-header.module.css";

export function PublicNav({ pathname }: { pathname: string }) {
  const mobileMenu = useRef<HTMLDetailsElement>(null);
  const [authMode, setAuthMode] = useState<AuthMode | null>(null);
  const [authNext, setAuthNext] = useState(pathname);
  const closeMobileMenu = () => mobileMenu.current?.removeAttribute("open");

  function openAuth(event: MouseEvent<HTMLAnchorElement>, mode: AuthMode) {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    closeMobileMenu();
    setAuthNext(`${window.location.pathname}${window.location.search}`);
    setAuthMode(mode);
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
          <Link aria-haspopup="dialog" className={styles.loginLink} href="/login" onClick={(event) => openAuth(event, "login")}>Log in</Link>
          <Link aria-haspopup="dialog" className={styles.joinButton} href="/register" onClick={(event) => openAuth(event, "register")}>Join</Link>
        </div>
      </div>

      <div className={styles.publicMobileShell}>
        <Link aria-haspopup="dialog" className={styles.mobileLoginLink} href="/login" onClick={(event) => openAuth(event, "login")}>Log in</Link>
        <Link aria-haspopup="dialog" className={styles.mobileJoinButton} href="/register" onClick={(event) => openAuth(event, "register")}>Join</Link>
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
      {authMode && (
        <AuthDialog
          mode={authMode}
          nextPath={authNext}
          onClose={() => setAuthMode(null)}
          onModeChange={setAuthMode}
        />
      )}
    </>
  );
}
