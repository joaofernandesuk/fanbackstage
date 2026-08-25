"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, CurrentUser } from "../lib/api";

type Notice = { id: string; title: string; body: string; target_path?: string; read_at?: string };

export function AppHeader() {
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [unread, setUnread] = useState(0);
  const [open, setOpen] = useState(false);
  const router = useRouter();
  useEffect(() => { api<CurrentUser>("/me").then(setUser).catch(() => setUser(null)); }, []);
  useEffect(() => { if (user) api<{ items: Notice[]; unread_count: number }>("/notifications").then((v) => { setNotices(v.items.slice(0, 5)); setUnread(v.unread_count); }).catch(() => undefined); }, [user]);
  async function logout() { await api("/auth/logout", { method: "POST" }); setUser(null); router.push("/"); }
  const roles = user?.roles ?? [];
  return <header><Link href="/" className="brand" aria-label="FanBackstage home">FanBackstage</Link>{user ? <><nav aria-label="Primary navigation"><Link href="/feed">Home</Link><Link href="/discover">Discover</Link><Link href="/live">Live</Link><Link href="/marketplace/orders">Marketplace</Link></nav><div className="nav-actions"><Link className="icon-link" href="/messages" aria-label="Messages">✦</Link><div className="notification-wrap"><button className="icon-button" aria-label={`Notifications${unread ? `, ${unread} unread` : ""}`} aria-expanded={open} onClick={() => setOpen(!open)}>♟{unread > 0 && <span className="badge">{unread > 9 ? "9+" : unread}</span>}</button>{open && <section className="notification-popover" aria-label="Recent notifications"><div className="popover-title"><strong>Notifications</strong><Link href="/notifications" onClick={() => setOpen(false)}>View all</Link></div>{notices.length ? notices.map((notice) => <Link className={`notice ${notice.read_at ? "" : "unread"}`} href={notice.target_path ?? "/notifications"} key={notice.id} onClick={() => setOpen(false)}><strong>{notice.title}</strong><span>{notice.body}</span></Link>) : <p className="empty">You’re all caught up.</p>}</section>}</div><details className="account-menu"><summary aria-label="Open account menu">{user.email.slice(0, 1).toUpperCase()}</summary><div><p>{user.email}</p><Link href="/account">Account</Link><Link href="/purchases">Purchases</Link>{roles.includes("creator") && <Link href="/creator-studio">Creator Studio</Link>}{roles.includes("manager") && <Link href="/groups">Groups</Link>}{(roles.includes("admin") || roles.includes("moderator")) && <Link href="/moderation">Moderation</Link>}<button onClick={logout}>Log out</button></div></details></div></> : <><nav aria-label="Public navigation"><Link href="/discover">Discover</Link><Link href="/live">Live</Link><Link href="/search">Creators</Link><Link href="/login">Log in</Link><Link className="button" href="/register">Join</Link></nav></>}</header>;
}
