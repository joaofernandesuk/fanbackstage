"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { api, type CurrentUser } from "../lib/api";
import { AccountMenu } from "./account-menu";
import { AuthNav } from "./auth-nav";
import { BrandLogo } from "./brand-logo";
import { MobileBottomNav } from "./mobile-bottom-nav";
import type { NavigationIdentity } from "./navigation-model";
import {
  NotificationPopover,
  type HeaderNotice,
} from "./notification-popover";
import { PublicNav } from "./public-nav";
import { MessageIcon, SearchIcon } from "./shell-icons";
import styles from "./app-header.module.css";

type NotificationPage = { items: HeaderNotice[]; unread_count: number };
type Conversation = { unread_count: number };
type CreatorIdentity = { username: string | null; display_name: string | null };
type AuthState = "loading" | "anonymous" | "authenticated";

export function AppHeader() {
  const pathname = usePathname();
  const router = useRouter();
  const [authState, setAuthState] = useState<AuthState>("loading");
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [creator, setCreator] = useState<CreatorIdentity | null>(null);
  const [notices, setNotices] = useState<HeaderNotice[]>([]);
  const [notificationUnread, setNotificationUnread] = useState(0);
  const [messageUnread, setMessageUnread] = useState(0);

  useEffect(() => {
    let active = true;
    api<CurrentUser>("/me")
      .then((nextUser) => {
        if (!active) return;
        setUser(nextUser);
        setAuthState("authenticated");
      })
      .catch(() => {
        if (!active) return;
        setUser(null);
        setCreator(null);
        setNotices([]);
        setNotificationUnread(0);
        setMessageUnread(0);
        setAuthState("anonymous");
      });
    return () => { active = false; };
  }, [pathname]);

  const rolesKey = user?.roles.join(",") ?? "";
  useEffect(() => {
    if (!user) return;
    let active = true;

    function refreshActivity() {
      void api<NotificationPage>("/notifications?limit=8")
        .then((page) => {
          if (!active) return;
          setNotices(page.items.slice(0, 8));
          setNotificationUnread(page.unread_count);
        })
        .catch(() => undefined);
      void api<Conversation[]>("/messages/conversations?limit=50")
        .then((conversations) => {
          if (active) {
            setMessageUnread(conversations.reduce((total, item) => total + item.unread_count, 0));
          }
        })
        .catch(() => undefined);
    }

    refreshActivity();
    const timer = window.setInterval(refreshActivity, 30_000);

    if (user.roles.includes("creator")) {
      void api<CreatorIdentity>("/creators/me")
        .then((profile) => { if (active) setCreator(profile); })
        .catch(() => { if (active) setCreator(null); });
    } else {
      setCreator(null);
    }

    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [user?.id, rolesKey]);

  const identity = useMemo<NavigationIdentity | null>(() => user ? ({
    email: user.email,
    roles: user.roles,
    creatorUsername: creator?.username,
  }) : null, [creator?.username, user]);

  async function logout() {
    try {
      await api("/auth/logout", { method: "POST" });
    } finally {
      setUser(null);
      setCreator(null);
      setNotices([]);
      setNotificationUnread(0);
      setMessageUnread(0);
      setAuthState("anonymous");
      router.push("/");
      router.refresh();
    }
  }

  async function markNoticeRead(id: string) {
    await api(`/notifications/${id}/read`, { method: "POST" });
    const readAt = new Date().toISOString();
    setNotices((current) => current.map((notice) => (
      notice.id === id ? { ...notice, read_at: readAt } : notice
    )));
    setNotificationUnread((current) => Math.max(0, current - 1));
  }

  async function markAllNoticesRead() {
    await api("/notifications/read-all", { method: "POST" });
    const readAt = new Date().toISOString();
    setNotices((current) => current.map((notice) => ({
      ...notice,
      read_at: notice.read_at || readAt,
    })));
    setNotificationUnread(0);
  }

  return (
    <>
      <header className={styles.header}>
        <div className={styles.headerInner}>
          <BrandLogo />
          {authState === "loading" && (
            <div aria-label="Loading navigation" className={styles.loadingNav} role="status" />
          )}
          {authState === "anonymous" && <PublicNav pathname={pathname} />}
          {authState === "authenticated" && identity && (
            <>
              <AuthNav pathname={pathname} />
              <div className={styles.headerActions}>
                <Link
                  aria-label="Search"
                  className={`${styles.iconControl} ${styles.searchControl}`}
                  href="/search"
                >
                  <SearchIcon className={styles.actionIcon} />
                </Link>
                <Link
                  aria-label={`Messages${messageUnread ? `, ${messageUnread} unread` : ""}`}
                  className={styles.messageControl}
                  href="/messages"
                >
                  <MessageIcon className={styles.actionIcon} />
                  {messageUnread > 0 && (
                    <span className={styles.unreadBadge}>
                      {messageUnread > 99 ? "99+" : messageUnread}
                    </span>
                  )}
                </Link>
                <NotificationPopover
                  notices={notices}
                  onMarkAllRead={markAllNoticesRead}
                  onRead={markNoticeRead}
                  unread={notificationUnread}
                />
                <AccountMenu
                  displayName={creator?.display_name}
                  identity={identity}
                  onLogout={logout}
                />
              </div>
            </>
          )}
        </div>
      </header>
      {authState === "authenticated" && identity && (
        <MobileBottomNav identity={identity} messageUnread={messageUnread} pathname={pathname} />
      )}
    </>
  );
}
