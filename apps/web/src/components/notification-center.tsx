"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "../lib/api";

type Notice = { id: string; notification_type: string; title: string; body: string; target_path?: string; read_at?: string };
export function NotificationCenter() {
  const [items, setItems] = useState<Notice[]>([]); const [unread, setUnread] = useState(0);
  const load = () => api<{items: Notice[]; unread_count: number}>("/notifications").then(v => { setItems(v.items); setUnread(v.unread_count); });
  useEffect(() => { load(); }, []);
  async function read(id: string) { await api(`/notifications/${id}/read`, { method: "POST" }); load(); }
  return <section className="card"><p className="eyebrow">NOTIFICATIONS</p><h1>Notifications</h1><p>{unread} unread</p><button onClick={() => api("/notifications/read-all", { method: "POST" }).then(load)}>Mark all read</button>{items.map(n => <article key={n.id}><h2>{n.title}</h2><p>{n.body}</p>{n.target_path && <Link href={n.target_path}>Open</Link>}{!n.read_at && <button onClick={() => read(n.id)}>Mark read</button>}</article>)}</section>;
}
