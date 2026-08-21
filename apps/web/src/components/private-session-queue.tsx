"use client";

import { useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";

type Request = { id: string; mode: string; per_minute_price_minor: number; minimum_charge_minor: number; currency: string; status: string };

export function PrivateSessionQueue() {
  const [requests, setRequests] = useState<Request[]>([]);
  const [message, setMessage] = useState("");
  async function refresh() { try { setRequests(await api<Request[]>("/live/private-requests/mine/creator")); } catch (caught) { setMessage(caught instanceof ApiError ? caught.message : "Unable to load private requests"); } }
  useEffect(() => { void refresh(); }, []);
  async function accept(request: Request) {
    try { const session = await api<{ status: string }>(`/live/private-requests/${request.id}/accept`, { method: "POST" }); setMessage(`Request accepted; server-side payment authorization is ${session.status}.`); await refresh(); }
    catch (caught) { setMessage(caught instanceof ApiError ? caught.message : "Unable to accept private request"); }
  }
  return <section className="card" aria-label="Private session queue"><p className="eyebrow">PRIVATE SESSIONS</p><h2>Queued requests</h2>{requests.map((request) => <article key={request.id}><p>{request.mode} · {request.per_minute_price_minor} {request.currency}/minute</p><button onClick={() => void accept(request)}>Accept request</button></article>)}{!requests.length && <p>No pending requests.</p>}{message && <p>{message}</p>}</section>;
}
