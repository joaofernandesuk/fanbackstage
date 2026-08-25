"use client";

import { useState } from "react";

import { ApiError, api } from "../../../lib/api";

export default function ModeratorConsentPage() {
  const [releaseId, setReleaseId] = useState(""); const [approved, setApproved] = useState(true); const [notice, setNotice] = useState(""); const [error, setError] = useState("");
  const decide = () => api<{ id: string; status: string }>(`/trust-safety/consent-releases/${releaseId}/verify?approved=${approved}`, { method: "POST" }).then((release) => { setNotice(`Release ${release.status}`); setError(""); }).catch((e) => setError(e instanceof ApiError ? e.message : "Unable to review release"));
  return <section className="card"><p className="eyebrow">TRUST & SAFETY</p><h1>Consent review</h1><p>Verification authority and self-verification prevention are enforced by the server. Sensitive evidence requires separate explicit access.</p><label>Release ID<input value={releaseId} onChange={(e) => setReleaseId(e.target.value)} /></label><label>Decision<select value={String(approved)} onChange={(e) => setApproved(e.target.value === "true")}><option value="true">Verify</option><option value="false">Reject</option></select></label><button type="button" disabled={!releaseId} onClick={() => void decide()}>Record review</button>{notice && <p role="status">{notice}</p>}{error && <p className="error">{error}</p>}</section>;
}
