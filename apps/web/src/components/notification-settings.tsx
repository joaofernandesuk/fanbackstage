"use client";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
type Preference = { category: string; email_enabled: boolean; in_app_enabled: boolean; consented_at?: string };
export function NotificationSettings() {
  const [prefs, setPrefs] = useState<Preference[]>([]); const load = () => api<Preference[]>("/notifications/preferences").then(setPrefs); useEffect(() => { load(); }, []);
  async function save(category: string, email_enabled: boolean) { await api(`/notifications/preferences/${category}`, { method: "PUT", body: JSON.stringify({ email_enabled, in_app_enabled: true, consent: category === "marketing" && email_enabled }) }); load(); }
  const marketing = prefs.find(p => p.category === "marketing");
  return <section className="card"><p className="eyebrow">SETTINGS</p><h1>Notification preferences</h1><p>Security and required transactional messages are always delivered.</p><label><input type="checkbox" checked={marketing?.email_enabled ?? false} onChange={e => save("marketing", e.target.checked)} /> Marketing email (explicit opt-in)</label><button onClick={() => api("/notifications/unsubscribe", { method: "POST" }).then(load)}>Unsubscribe from marketing</button></section>;
}
