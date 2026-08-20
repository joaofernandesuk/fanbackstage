"use client";

import { FormEvent, useState } from "react";

import { api, ApiError } from "../lib/api";

const durations = ["month_1", "month_3", "month_6", "month_12"];

export function SubscriptionPromotionSettings() {
  const [message, setMessage] = useState("");

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const rules = durations
      .filter((duration) => form.get(`${duration}-included`) === "on")
      .map((duration) => ({
        duration,
        discount_basis_points: Number(form.get(`${duration}-discount`)),
      }));
    if (!rules.length) { setMessage("Choose at least one subscription duration."); return; }
    try {
      await api("/creator/subscription-promotions", {
        method: "POST",
        body: JSON.stringify({
          name: form.get("promotion-name"),
          eligibility: form.get("promotion-eligibility"),
          renewal_scope: form.get("promotion-renewal-scope"),
          enabled: form.get("promotion-enabled") === "on",
          start_at: new Date(String(form.get("promotion-start-at"))).toISOString(),
          ...(form.get("promotion-end-at") ? { end_at: new Date(String(form.get("promotion-end-at"))).toISOString() } : {}),
          rules,
        }),
      });
      setMessage("Subscription promotion created.");
      event.currentTarget.reset();
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "Unable to create promotion");
    }
  }

  return <section><h2>Subscription promotions</h2><form onSubmit={save}><label>Promotion name<input name="promotion-name" maxLength={160} required /></label><label>Eligibility<select name="promotion-eligibility" defaultValue="all_eligible"><option value="new_subscriber">New subscriber</option><option value="all_eligible">All eligible</option><option value="reactivation">Reactivation</option></select></label><label>Renewal scope<select name="promotion-renewal-scope" defaultValue="initial_only"><option value="initial_only">Initial period only</option><option value="initial_and_renewal">Initial period and renewals</option></select></label><label>Starts at<input name="promotion-start-at" type="datetime-local" required /></label><label>Ends at (optional)<input name="promotion-end-at" type="datetime-local" /></label><label><input name="promotion-enabled" type="checkbox" defaultChecked /> Enabled</label>{durations.map((duration) => <fieldset key={duration}><legend>{duration.replace("month_", "")} month{duration === "month_1" ? "" : "s"}</legend><label><input name={`${duration}-included`} type="checkbox" /> Include duration</label><label>Discount (basis points)<input name={`${duration}-discount`} type="number" min="0" max="9999" defaultValue="0" /></label></fieldset>)}<button>Create subscription promotion</button></form>{message && <p className="error">{message}</p>}</section>;
}
