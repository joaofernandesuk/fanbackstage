"use client";

import { useState } from "react";

import { ApiError, api } from "../../lib/api";

export default function AppealsPage() {
  const [actionId, setActionId] = useState(""); const [reason, setReason] = useState(""); const [notice, setNotice] = useState(""); const [error, setError] = useState("");
  const submit = () => api<{ id: string; status: string; deadline: string }>(`/trust-safety/actions/${actionId}/appeals`, { method: "POST", body: JSON.stringify({ reason }) }).then((appeal) => { setNotice(`Appeal ${appeal.status}. Deadline: ${new Date(appeal.deadline).toLocaleString()}`); setError(""); }).catch((e) => setError(e instanceof ApiError ? e.message : "Unable to submit appeal"));
  return <section className="card"><p className="eyebrow">TRUST & SAFETY</p><h1>Appeal an enforcement decision</h1><p>Only eligible decisions may be appealed. Eligibility, deadline and review separation are verified by the server.</p><label>Enforcement action ID<input value={actionId} onChange={(event) => setActionId(event.target.value)} /></label><label>Appeal reason<textarea value={reason} onChange={(event) => setReason(event.target.value)} /></label><button type="button" disabled={!actionId || !reason.trim()} onClick={() => void submit()}>Submit appeal</button>{notice && <p role="status">{notice}</p>}{error && <p className="error">{error}</p>}</section>;
}
