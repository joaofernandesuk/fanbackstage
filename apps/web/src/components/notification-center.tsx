"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { api, ApiError } from "../lib/api";
import { EmptyState, Skeleton, useLoginGate } from "./consumer-ui";
import { NotificationTypeIcon } from "./shell-icons";
import styles from "./social-surface.module.css";

type Notice = {
  id: string;
  notification_type: string;
  title: string;
  body: string;
  target_path: string | null;
  created_at: string;
  read_at: string | null;
};
type NotificationPage = { items: Notice[]; unread_count: number };

function relativeTime(value: string) {
  const date = new Date(value);
  const elapsed = Math.max(0, Date.now() - date.getTime());
  const minutes = Math.max(1, Math.floor(elapsed / 60_000));
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days} day${days === 1 ? "" : "s"} ago`;
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric", year: "numeric" }).format(date);
}

function safeTarget(path: string | null) {
  return path && path.startsWith("/") && !path.startsWith("//") ? path : null;
}

export function NotificationCenter() {
  const { authenticated, loading: authLoading } = useLoginGate();
  const [items, setItems] = useState<Notice[]>([]);
  const [unread, setUnread] = useState(0);
  const [filter, setFilter] = useState<"all" | "unread">("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  async function load() {
    try {
      const page = await api<NotificationPage>("/notifications?limit=100");
      setItems(page.items);
      setUnread(page.unread_count);
      setError("");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to load notifications");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (authenticated) void load();
    else if (!authLoading) setLoading(false);
  }, [authenticated, authLoading]);

  async function read(id: string) {
    await api(`/notifications/${id}/read`, { method: "POST" });
    const now = new Date().toISOString();
    setItems((current) => current.map((notice) => notice.id === id ? { ...notice, read_at: now } : notice));
    setUnread((current) => Math.max(0, current - 1));
  }

  async function readAll() {
    await api("/notifications/read-all", { method: "POST" });
    const now = new Date().toISOString();
    setItems((current) => current.map((notice) => ({ ...notice, read_at: notice.read_at || now })));
    setUnread(0);
  }

  const visible = useMemo(() => filter === "unread" ? items.filter((item) => !item.read_at) : items, [filter, items]);

  if (loading || authLoading) {
    return <div className={styles.notificationPage}><Skeleton lines={4} /><Skeleton lines={4} /></div>;
  }

  if (!authenticated) {
    return <EmptyState action={<Link className={styles.primaryLink} href="/login?next=%2Fnotifications">Log in</Link>} body="Sign in to see private account, creator, purchase, and social updates." title="Notifications are personal" />;
  }

  return (
    <section aria-labelledby="notifications-title" className={styles.notificationPage}>
      <header className={styles.notificationToolbar}>
        <div>
          <p className="eyebrow">ACTIVITY</p>
          <h1 id="notifications-title">Notifications</h1>
          <p>{unread ? `${unread} unread update${unread === 1 ? "" : "s"}` : "You’re all caught up. 0 unread updates."}</p>
        </div>
        {unread > 0 && <button className={styles.markAllButton} onClick={() => void readAll()} type="button">Mark all read</button>}
      </header>

      <div aria-label="Filter notifications" className={styles.notificationFilters}>
        <button aria-pressed={filter === "all"} onClick={() => setFilter("all")} type="button">All</button>
        <button aria-pressed={filter === "unread"} onClick={() => setFilter("unread")} type="button">Unread {unread ? `(${unread})` : ""}</button>
      </div>

      {error && <p className={styles.inlineMessage} role="status">{error}</p>}
      {visible.length ? (
        <div className={styles.notificationList}>
          {visible.map((notice) => {
            const target = safeTarget(notice.target_path);
            return (
              <article className={`${styles.notificationItem} ${!notice.read_at ? styles.notificationUnread : ""}`} key={notice.id}>
                <span className={styles.notificationIcon}><NotificationTypeIcon type={notice.notification_type} /></span>
                <div className={styles.notificationCopy}>
                  <h2>{notice.title}</h2>
                  <p>{notice.body}</p>
                  <time dateTime={notice.created_at}>{relativeTime(notice.created_at)}</time>
                </div>
                <div className={styles.notificationActions}>
                  {target && <Link href={target} onClick={() => !notice.read_at && void read(notice.id)}>Open</Link>}
                  {!notice.read_at && <button onClick={() => void read(notice.id)} type="button">Mark read</button>}
                </div>
              </article>
            );
          })}
        </div>
      ) : (
        <EmptyState
          action={filter === "unread" && items.length ? <button className={styles.secondaryLink} onClick={() => setFilter("all")} type="button">Show all activity</button> : <Link className={styles.secondaryLink} href="/discover">Explore creators</Link>}
          body={filter === "unread" ? "There are no unread updates waiting for you." : "New creator, purchase, message, and live activity will appear here."}
          title="You’re all caught up"
        />
      )}
    </section>
  );
}
