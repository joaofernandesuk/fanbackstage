"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import { BellIcon, CheckIcon, NotificationTypeIcon } from "./shell-icons";
import styles from "./app-header.module.css";

export type HeaderNotice = {
  id: string;
  notification_type: string;
  title: string;
  body: string;
  target_path?: string | null;
  created_at: string;
  read_at?: string | null;
};

export function relativeNotificationTime(value: string, now = Date.now()) {
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return "Recently";
  const seconds = Math.max(0, Math.floor((now - timestamp) / 1000));
  if (seconds < 60) return "Now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  const weeks = Math.floor(days / 7);
  return weeks < 5 ? `${weeks}w` : `${Math.floor(days / 30)}mo`;
}

function safeTargetPath(value?: string | null) {
  return value?.startsWith("/") && !value.startsWith("//") ? value : "/notifications";
}

export function NotificationPopover({
  notices,
  onMarkAllRead,
  onRead,
  unread,
}: {
  notices: HeaderNotice[];
  onMarkAllRead: () => Promise<void>;
  onRead: (id: string) => Promise<void>;
  unread: number;
}) {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function close(event: PointerEvent) {
      if (wrap.current && !wrap.current.contains(event.target as Node)) setOpen(false);
    }
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        wrap.current?.querySelector<HTMLButtonElement>("button")?.focus();
      }
    }
    document.addEventListener("pointerdown", close);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", close);
      document.removeEventListener("keydown", escape);
    };
  }, []);

  return (
    <div className={styles.notificationWrap} ref={wrap}>
      <button
        aria-controls="header-notification-popover"
        aria-expanded={open}
        aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`}
        className={styles.iconControl}
        onClick={() => setOpen((value) => !value)}
        type="button"
      >
        <BellIcon className={styles.actionIcon} />
        {unread > 0 && <span className={styles.unreadBadge}>{unread > 99 ? "99+" : unread}</span>}
      </button>
      {open && (
        <section
          aria-label="Recent notifications"
          className={styles.notificationPanel}
          id="header-notification-popover"
        >
          <div className={styles.notificationHeading}>
            <div>
              <strong>Notifications</strong>
              <span>{unread ? `${unread} unread` : "You’re up to date"}</span>
            </div>
            {unread > 0 && (
              <button
                className={styles.markAllButton}
                onClick={() => void onMarkAllRead()}
                type="button"
              >
                <CheckIcon className={styles.markAllIcon} />
                Mark all read
              </button>
            )}
          </div>
          <div className={styles.notificationList}>
            {notices.length > 0 ? notices.map((notice) => (
              <Link
                className={`${styles.notice} ${notice.read_at ? "" : styles.noticeUnread}`}
                href={safeTargetPath(notice.target_path)}
                key={notice.id}
                onClick={() => {
                  if (!notice.read_at) void onRead(notice.id);
                  setOpen(false);
                }}
              >
                <span className={styles.noticeIcon}>
                  <NotificationTypeIcon className={styles.noticeTypeIcon} type={notice.notification_type} />
                </span>
                <span className={styles.noticeCopy}>
                  <span className={styles.noticeTopline}>
                    <strong>{notice.title}</strong>
                    <time dateTime={notice.created_at}>{relativeNotificationTime(notice.created_at)}</time>
                  </span>
                  <span className={styles.noticeBody}>{notice.body}</span>
                </span>
                {!notice.read_at && <span aria-label="Unread" className={styles.noticeDot} />}
              </Link>
            )) : (
              <div className={styles.notificationEmpty}>
                <BellIcon className={styles.emptyIcon} />
                <strong>You’re all caught up.</strong>
                <span>New activity will appear here.</span>
              </div>
            )}
          </div>
          <Link className={styles.viewAllLink} href="/notifications" onClick={() => setOpen(false)}>
            View all notifications
          </Link>
        </section>
      )}
    </div>
  );
}
