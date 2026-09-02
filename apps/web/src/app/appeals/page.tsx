"use client";

import { useCallback, useEffect, useState } from "react";

import styles from "../../components/operations-workspace.module.css";
import { ApiError, api } from "../../lib/api";

type Item = {
  action_id: string; action_type: string; action_date: string; content_title: string;
  deadline: string; eligible: boolean;
  appeal: { id: string; status: string; outcome: string | null } | null;
};

export default function AppealsPage() {
  const [items, setItems] = useState<Item[]>([]);
  const [selected, setSelected] = useState<Item | null>(null);
  const [reason, setReason] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const load = useCallback(async () => {
    try {
      const result = await api<{ items: Item[] }>("/trust-safety/appeals/mine/eligible");
      setItems(result.items); setError("");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to load appeal history.");
    }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const submit = async () => {
    if (!selected) return;
    try {
      await api(`/trust-safety/actions/${selected.action_id}/appeals`, { method: "POST", body: JSON.stringify({ reason }) });
      setNotice("Your appeal was submitted for independent review."); setSelected(null); setReason(""); await load();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to submit appeal.");
    }
  };
  return <main className={styles.page}>
    <header className={styles.hero}><p className="eyebrow">TRUST &amp; SAFETY</p><h1>Your appeals</h1><p>Select an eligible enforcement action. Deadlines and duplicate prevention are enforced by the server.</p></header>
    <section className={styles.panel}>{error && <p className={styles.error} role="alert">{error}</p>}{notice && <p className={styles.notice} role="status">{notice}</p>}
      <div className={styles.tableWrap}><table className={styles.table}><thead><tr><th>Content</th><th>Action</th><th>Date</th><th>Deadline</th><th>State</th></tr></thead><tbody>{items.map((item) => <tr key={item.action_id}><td><button className={styles.rowButton} disabled={!item.eligible} onClick={() => setSelected(item)}><strong>{item.content_title}</strong><span>{item.eligible ? "Appeal this action" : "Not currently available"}</span></button></td><td>{item.action_type.replaceAll("_", " ")}</td><td>{new Date(item.action_date).toLocaleDateString()}</td><td>{new Date(item.deadline).toLocaleString()}</td><td><span className={styles.status}>{item.appeal?.status ?? (item.eligible ? "eligible" : "expired")}</span></td></tr>)}</tbody></table>{!items.length && <p className={styles.empty}>There are no enforcement actions in your appeal history.</p>}</div>
      {selected && <div className={styles.decision}><h2>Appeal {selected.action_type.replaceAll("_", " ")}</h2><p>{selected.content_title} · deadline {new Date(selected.deadline).toLocaleString()}</p><label>Why should this decision be reviewed?<textarea value={reason} onChange={(event) => setReason(event.target.value)} /></label><button disabled={!reason.trim()} onClick={() => void submit()}>Submit appeal</button><button className={styles.secondary} onClick={() => setSelected(null)}>Cancel</button></div>}
    </section>
  </main>;
}
