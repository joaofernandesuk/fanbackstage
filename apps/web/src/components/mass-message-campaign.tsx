"use client";

import { FormEvent, useState } from "react";

import { api, ApiError } from "../lib/api";

const segments = ["followers", "active_subscribers", "expired_subscribers", "previous_customers"];

export function MassMessageCampaign() {
  const [message, setMessage] = useState("");

  async function createCampaign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const scheduled = String(form.get("scheduled_at") || "");
    try {
      const campaign = await api<{ id: string; status: string }>("/messages/campaigns", {
        method: "POST",
        body: JSON.stringify({
          audience_segment: form.get("audience_segment"),
          body: form.get("body"),
          scheduled_at: scheduled ? new Date(scheduled).toISOString() : undefined,
        }),
      });
      setMessage(`Campaign ${campaign.status}.`);
      event.currentTarget.reset();
    } catch (requestError) {
      setMessage(requestError instanceof ApiError ? requestError.message : "Campaign could not be created");
    }
  }

  return (
    <section>
      <h2>Mass message campaign</h2>
      <p>Recipients are snapshotted now. Any later block still prevents delivery.</p>
      <form onSubmit={createCampaign}>
        <label>Audience<select name="audience_segment">{segments.map((segment) => <option key={segment} value={segment}>{segment.replaceAll("_", " ")}</option>)}</select></label>
        <label>Message<textarea name="body" required maxLength={4000} /></label>
        <label>Schedule (converted to UTC; optional)<input name="scheduled_at" type="datetime-local" /></label>
        <button>Create campaign</button>
      </form>
      {message && <p>{message}</p>}
    </section>
  );
}
