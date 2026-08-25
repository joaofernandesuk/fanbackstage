"use client";

import { useState } from "react";

import { ApiError, api } from "../../../lib/api";

type Release = { id: string; status: string; release_type: string; effective_until: string | null; revoked_at: string | null; supersedes_release_id: string | null };

export default function CreatorConsentPage() {
  const [creatorId, setCreatorId] = useState(""); const [contentId, setContentId] = useState(""); const [participant, setParticipant] = useState(""); const [releases, setReleases] = useState<Release[]>([]); const [error, setError] = useState("");
  const load = () => api<Release[]>(`/trust-safety/creators/${creatorId}/consent-releases`).then(setReleases).catch((e) => setError(e instanceof ApiError ? e.message : "Unable to load releases"));
  const submit = () => api(`/trust-safety/creators/${creatorId}/consent-releases`, { method: "POST", body: JSON.stringify({ release_type: "co_performer_release", participant_reference: participant, content_ids: [contentId] }) }).then(() => load()).catch((e) => setError(e instanceof ApiError ? e.message : "Unable to submit release"));
  const revoke = (id: string) => api(`/trust-safety/consent-releases/${id}/revoke`, { method: "POST" }).then(() => load()).catch((e) => setError(e instanceof ApiError ? e.message : "Unable to revoke release"));
  return <section className="card"><p className="eyebrow">CREATOR STUDIO</p><h1>Consent releases</h1><p>Releases remain pending until an authorized Trust & Safety reviewer verifies them.</p><label>Creator ID<input value={creatorId} onChange={(e) => setCreatorId(e.target.value)} /></label><button type="button" disabled={!creatorId} onClick={() => void load()}>Load releases</button><label>Linked content ID<input value={contentId} onChange={(e) => setContentId(e.target.value)} /></label><label>Participant reference<input value={participant} onChange={(e) => setParticipant(e.target.value)} /></label><button type="button" disabled={!creatorId || !contentId || !participant} onClick={() => void submit()}>Submit release</button><ul>{releases.map((release) => <li key={release.id}>{release.release_type} · {release.status}{release.effective_until ? ` · expires ${new Date(release.effective_until).toLocaleString()}` : ""}{release.status === "verified" && <button type="button" onClick={() => void revoke(release.id)}>Revoke</button>}</li>)}</ul>{error && <p className="error">{error}</p>}</section>;
}
