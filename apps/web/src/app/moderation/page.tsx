"use client";

import { useEffect, useState } from "react";

import { ApiError, api } from "../../lib/api";

type Case = { id: string; public_id: string; status: string; severity: string; queue: string; assigned_moderator_id: string | null };

export default function ModerationPage() {
  const [cases, setCases] = useState<Case[]>([]); const [error, setError] = useState("");
  useEffect(() => { api<Case[]>("/trust-safety/cases").then(setCases).catch((e) => setError(e instanceof ApiError ? e.message : "Unable to load moderation cases")); }, []);
  return <section className="card"><p className="eyebrow">TRUST & SAFETY</p><h1>Moderation queue</h1><p>Case access and actions are authorized by the server.</p>{error && <p className="error">{error}</p>}<ul>{cases.map((item) => <li key={item.id}><strong>{item.public_id}</strong> — {item.severity} · {item.queue} · {item.status}{item.assigned_moderator_id ? " · assigned" : " · unassigned"}</li>)}</ul>{!error && !cases.length && <p>No accessible cases.</p>}</section>;
}
