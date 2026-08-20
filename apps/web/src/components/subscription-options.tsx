"use client";

import { useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";

type Option = { duration: string; base_amount_minor: number; effective_amount_minor: number; currency: string; discount_basis_points: number };

export function SubscriptionOptions({ username, creatorId }: { username: string; creatorId: string }) {
  const [options, setOptions] = useState<Option[]>([]);
  const [error, setError] = useState("");
  const [working, setWorking] = useState("");
  useEffect(() => { api<Option[]>(`/creators/${username}/subscription-options`).then(setOptions).catch(() => setOptions([])); }, [username]);
  async function subscribe(duration: string) {
    setWorking(duration); setError("");
    try {
      const started = await api<{ id: string }> (`/subscriptions/creator/${creatorId}`, { method: "POST", headers: { "Idempotency-Key": crypto.randomUUID() }, body: JSON.stringify({ duration }) });
      await api(`/subscriptions/${started.id}/complete-development`, { method: "POST" });
      setError("Subscription is active.");
    } catch (e) { setError(e instanceof ApiError ? e.message : "Subscription could not be started"); } finally { setWorking(""); }
  }
  if (!options.length) return null;
  return <section><h2>Subscriptions</h2>{options.map((option) => <article className="card" key={option.duration}><h3>{option.duration.replace("month_", "")} month{option.duration === "month_1" ? "" : "s"}</h3><p>{option.effective_amount_minor} {option.currency}{option.discount_basis_points > 0 && <> <s>{option.base_amount_minor}</s> ({option.discount_basis_points / 100}% off)</>}</p><button disabled={working === option.duration} onClick={() => subscribe(option.duration)}>{working === option.duration ? "Starting…" : "Subscribe"}</button></article>)}{error && <p className="error">{error}</p>}</section>;
}
