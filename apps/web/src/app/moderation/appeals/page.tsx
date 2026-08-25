"use client";

import { useState } from "react";

import { ApiError, api } from "../../../lib/api";

export default function ModeratorAppealsPage() {
  const [appealId, setAppealId] = useState(""); const [outcome, setOutcome] = useState("upheld"); const [reason, setReason] = useState(""); const [notice, setNotice] = useState(""); const [error, setError] = useState("");
  const decide = () => api<{ id: string; status: string }>(`/trust-safety/appeals/${appealId}/decision`, { method: "POST", body: JSON.stringify({ outcome, reason }) }).then((appeal) => { setNotice(`Appeal ${appeal.status}`); setError(""); }).catch((e) => setError(e instanceof ApiError ? e.message : "Unable to decide appeal"));
  return <section className="card"><p className="eyebrow">TRUST & SAFETY</p><h1>Appeal review</h1><p>Final review authority, the 30-day rule and HIGH/CRITICAL reviewer separation are enforced by the server.</p><label>Appeal ID<input value={appealId} onChange={(event) => setAppealId(event.target.value)} /></label><label>Outcome<select value={outcome} onChange={(event) => setOutcome(event.target.value)}><option value="upheld">Uphold</option><option value="overturned">Overturn</option><option value="partially_overturned">Partially overturn</option></select></label><label>Decision reason<textarea value={reason} onChange={(event) => setReason(event.target.value)} /></label><button type="button" disabled={!appealId || !reason.trim()} onClick={() => void decide()}>Record decision</button>{notice && <p role="status">{notice}</p>}{error && <p className="error">{error}</p>}</section>;
}
