"use client";

import { useEffect, useRef, useState } from "react";
import { Room } from "livekit-client";

import { api, ApiError } from "../lib/api";

type PrivateSession = {
  id: string;
  status: string;
  mode: string;
  per_minute_price_minor: number;
  minimum_charge_minor: number;
  currency: string;
  billable_seconds: number;
  payment_attempt_id: string | null;
};
type Token = { provider_url: string; token: string };

/**
 * Private-room transport is deliberately separate from the server's session
 * state. LiveKit's signed lifecycle events, not these buttons, determine
 * ready/connecting/active/reconnecting and billing transitions.
 */
export function PrivateSessionRoom() {
  const [sessions, setSessions] = useState<PrivateSession[]>([]);
  const [message, setMessage] = useState("");
  const providerRoom = useRef<Room | null>(null);

  async function refresh() {
    try {
      setSessions(await api<PrivateSession[]>("/live/private-sessions/mine"));
    } catch (caught) {
      setMessage(caught instanceof ApiError ? caught.message : "Unable to load private sessions");
    }
  }

  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), 5_000);
    return () => {
      clearInterval(timer);
      providerRoom.current?.disconnect();
    };
  }, []);

  async function authorize(session: PrivateSession) {
    if (!session.payment_attempt_id) return;
    try {
      // The development provider endpoint exercises the same signed Phase 3
      // settlement path used by its webhook; production checkout stays owned
      // by the configured payment provider.
      await api(`/payments/development/${session.payment_attempt_id}/complete`, { method: "POST" });
      setMessage("Payment authorization verified. You can now join the private room.");
      await refresh();
    } catch (caught) {
      setMessage(caught instanceof ApiError ? caught.message : "Payment authorization failed");
    }
  }

  async function join(session: PrivateSession) {
    try {
      providerRoom.current?.disconnect();
      const authorization = await api<Token>(`/live/private-sessions/${session.id}/token`, { method: "POST" });
      const room = new Room();
      await room.connect(authorization.provider_url, authorization.token);
      await room.localParticipant.enableCameraAndMicrophone();
      providerRoom.current = room;
      setMessage("Connected to the private room. Billing begins only after LiveKit confirms every required participant.");
    } catch (caught) {
      setMessage(caught instanceof ApiError ? caught.message : "Unable to join the private room");
    }
  }

  async function end(session: PrivateSession) {
    try {
      providerRoom.current?.disconnect();
      providerRoom.current = null;
      await api(`/live/private-sessions/${session.id}/end`, { method: "POST" });
      setMessage("Private session ended; final settlement is server-authoritative.");
      await refresh();
    } catch (caught) {
      setMessage(caught instanceof ApiError ? caught.message : "Unable to end the private session");
    }
  }

  return <section className="card" aria-label="Private rooms">
    <p className="eyebrow">PRIVATE ROOMS</p><h2>Your private sessions</h2>
    {!sessions.length && <p>No private sessions are ready.</p>}
    {sessions.map((session) => <article key={session.id}>
      <p>{session.mode} · {session.status} · {session.per_minute_price_minor} {session.currency}/minute</p>
      <p>Authoritative billable time: {session.billable_seconds} seconds.</p>
      {session.status === "awaiting_payment_authorization" && <button onClick={() => void authorize(session)}>Confirm payment authorization</button>}
      {["ready", "connecting", "active", "reconnecting"].includes(session.status) && <><button onClick={() => void join(session)}>Join private room</button><button onClick={() => void end(session)}>End private session</button></>}
    </article>)}
    {message && <p>{message}</p>}
  </section>;
}
