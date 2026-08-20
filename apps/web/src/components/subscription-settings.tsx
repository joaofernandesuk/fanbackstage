"use client";

import { FormEvent, useState } from "react";

import { api, ApiError } from "../lib/api";

const durations = ["month_1", "month_3", "month_6", "month_12"];

export function SubscriptionSettings() {
  const [message, setMessage] = useState("");
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    const prices = durations.map((duration) => ({ duration, amount_minor: Number(form.get(`${duration}-price`)), enabled: form.get(`${duration}-enabled`) === "on" }));
    try { await api("/creator/subscription-plan", { method: "PUT", body: JSON.stringify({ currency: String(form.get("currency") || "EUR").toUpperCase(), enabled: form.get("plan-enabled") === "on", prices }) }); setMessage("Subscription plan saved."); } catch (e) { setMessage(e instanceof ApiError ? e.message : "Unable to save plan"); }
  }
  return <section><h2>Subscription plans</h2><form onSubmit={save}><label><input name="plan-enabled" type="checkbox" defaultChecked /> Enable subscriptions</label><label>Currency<input name="currency" defaultValue="EUR" maxLength={3} /></label>{durations.map((duration) => <fieldset key={duration}><legend>{duration.replace("month_", "")} month{duration === "month_1" ? "" : "s"}</legend><label>Price (minor units)<input name={`${duration}-price`} type="number" min="1" defaultValue="999" required /></label><label><input name={`${duration}-enabled`} type="checkbox" defaultChecked /> Enabled</label></fieldset>)}<button>Save subscription plan</button></form>{message && <p>{message}</p>}</section>;
}
