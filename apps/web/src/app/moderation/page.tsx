"use client";

import { useEffect, useState } from "react";

import { ApiError, api } from "../../lib/api";

type Case = { id: string; public_id: string; status: string; severity: string; queue: string; assigned_moderator_id: string | null };
type Detail = { public_id: string; status: string; severity: string; priority: number; queue: string; target_type: string; report_count: number; notes: { id: string; body: string }[]; actions: { id: string; type: string; reason: string }[]; safe_evidence: { id: string; source_type: string; safe_reference: string | null }[] };

export default function ModerationPage() {
  const [cases, setCases] = useState<Case[]>([]); const [detail, setDetail] = useState<Detail | null>(null); const [error, setError] = useState("");
  useEffect(() => { api<Case[]>("/trust-safety/cases").then(setCases).catch((e) => setError(e instanceof ApiError ? e.message : "Unable to load moderation cases")); }, []);
  return <section className="card"><p className="eyebrow">TRUST & SAFETY</p><h1>Moderation queue</h1><p>Case access and actions are authorized by the server.</p>{error && <p className="error">{error}</p>}<ul>{cases.map((item) => <li key={item.id}><button type="button" onClick={() => api<Detail>(`/trust-safety/cases/${item.id}`).then(setDetail).catch((e) => setError(e instanceof ApiError ? e.message : "Unable to load case"))}><strong>{item.public_id}</strong> — {item.severity} · {item.queue} · {item.status}</button></li>)}</ul>{detail && <article><h2>{detail.public_id}</h2><p>{detail.severity} · priority {detail.priority} · {detail.queue} · {detail.status}</p><p>Target: {detail.target_type} · Reports: {detail.report_count}</p><h3>Safe evidence</h3><ul>{detail.safe_evidence.map((item) => <li key={item.id}>{item.source_type}{item.safe_reference ? " · reference available" : ""}</li>)}</ul><h3>Notes</h3><ul>{detail.notes.map((item) => <li key={item.id}>{item.body}</li>)}</ul><h3>Actions</h3><ul>{detail.actions.map((item) => <li key={item.id}>{item.type} · {item.reason}</li>)}</ul></article>}{!error && !cases.length && <p>No accessible cases.</p>}</section>;
}
