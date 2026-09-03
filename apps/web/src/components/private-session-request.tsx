"use client";

import { FormEvent, useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";
import { formatMoney } from "../lib/public-api";

type Request = { id: string; mode: string; per_minute_price_minor: number; minimum_charge_minor: number; currency: string; status: string; invitation_status: string; invited_viewer_label: string | null };
type Candidate = { user_id: string; label: string };

export function PrivateSessionRequest({ creatorId }: { creatorId: string }) {
  const [request, setRequest] = useState<Request | null>(null); const [candidates, setCandidates] = useState<Candidate[]>([]); const [message, setMessage] = useState(""); const [mode, setMode] = useState("one_to_one");
  useEffect(() => { api<Candidate[]>(`/live/creators/${creatorId}/private-invite-candidates`).then(setCandidates).catch(() => setCandidates([])); }, [creatorId]);
  async function requestSession(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget); const mode = String(form.get("mode")); const invited = String(form.get("invited") || "");
    try {
      const started = await api<Request>(`/live/creators/${creatorId}/private-requests`, { method: "POST", body: JSON.stringify({ mode, ...(mode === "two_to_one" ? { invited_user_id: invited } : {}) }) });
      setRequest(started); setMessage(mode === "two_to_one" ? `Invitation sent to ${started.invited_viewer_label}. The creator cannot accept and payment cannot start until they accept.` : `Request queued at ${formatMoney(started.per_minute_price_minor, started.currency)}/minute; payment starts only after creator acceptance.`);
    } catch (caught) { setMessage(caught instanceof ApiError ? caught.message : "Unable to request a private session"); }
  }
  return <section aria-label="Private session"><h2>Private session</h2>{request ? <p>{message}</p> : <form onSubmit={requestSession}><label>Session type<select name="mode" value={mode} onChange={(event) => setMode(event.target.value)}><option value="one_to_one">1-to-1</option><option value="two_to_one">2-to-1 with another fan</option></select></label><label>Second fan for 2-to-1<select name="invited" defaultValue=""><option value="">Choose an eligible fan</option>{candidates.map((candidate) => <option key={candidate.user_id} value={candidate.user_id}>{candidate.label}</option>)}</select></label><p>Only a bounded list of eligible fans following this creator is shown. The invited fan has no payment responsibility.</p><button>{mode === "two_to_one" ? "Request 2-to-1 session" : "Request 1:1 session"}</button></form>}<p>Private requests may queue during a public Live, but activation waits for all required decisions and server-confirmed payment.</p></section>;
}
