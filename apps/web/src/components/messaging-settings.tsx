"use client";

import { FormEvent, useState } from "react";
import { api, ApiError } from "../lib/api";

export function MessagingSettings() {
  const [message, setMessage] = useState("");
  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    const fee = String(form.get("send_fee_minor") || "");
    try { await api("/messages/settings", { method: "PUT", body: JSON.stringify({ permission: form.get("permission"), send_fee_minor: fee ? Number(fee) : undefined, send_fee_currency: fee ? String(form.get("currency") || "EUR").toUpperCase() : undefined, subscribers_free: form.get("subscribers_free") === "on" }) }); setMessage("Messaging settings saved."); } catch (error) { setMessage(error instanceof ApiError ? error.message : "Unable to save messaging settings"); }
  }
  return <section><h2>Messaging settings</h2><form onSubmit={save}><label>Who can message you<select name="permission"><option value="anyone">Anyone</option><option value="followers">Followers</option><option value="subscribers">Subscribers</option><option value="previous_customers">Previous customers</option><option value="nobody">Nobody</option></select></label><label>Message fee (minor units; optional)<input name="send_fee_minor" type="number" min="1" step="1" /></label><label>Currency<input name="currency" defaultValue="EUR" maxLength={3} /></label><label><input name="subscribers_free" type="checkbox" defaultChecked /> Subscribers message free</label><button>Save messaging settings</button></form>{message && <p>{message}</p>}</section>;
}
