"use client";

import { useState } from "react";

import { api, ApiError } from "../lib/api";
import { formatMoney } from "../lib/public-api";

type Request = { id: string; mode: string; per_minute_price_minor: number; minimum_charge_minor: number; currency: string; status: string };

export function PrivateSessionRequest({ creatorId }: { creatorId: string }) {
  const [request, setRequest] = useState<Request | null>(null);
  const [message, setMessage] = useState("");

  async function requestSession() {
    try {
      const started = await api<Request>(`/live/creators/${creatorId}/private-requests`, { method: "POST", body: JSON.stringify({ mode: "one_to_one" }) });
      setRequest(started); setMessage(`Request queued. Price is ${formatMoney(started.per_minute_price_minor, started.currency)}/minute; minimum ${formatMoney(started.minimum_charge_minor, started.currency)}. The creator must accept before payment authorization.`);
    } catch (caught) { setMessage(caught instanceof ApiError ? caught.message : "Unable to request a private session"); }
  }

  return <section aria-label="Private session"><h2>Private session</h2>{request ? <p>{message}</p> : <button onClick={() => void requestSession()}>Request 1:1 session</button>}<p>Private requests can queue while a creator is live, but they cannot be accepted until the public live ends.</p></section>;
}
