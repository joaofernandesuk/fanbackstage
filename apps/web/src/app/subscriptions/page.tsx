"use client";

import { useEffect, useState } from "react";

import { api, ApiError } from "../../lib/api";

type Subscription = {
  id: string;
  creator_id: string;
  duration: string;
  status: string;
  currency: string;
  auto_renew: boolean;
  cancel_at_period_end: boolean;
  current_period_end: string | null;
};

export default function SubscriptionsPage() {
  const [subscriptions, setSubscriptions] = useState<Subscription[]>([]);
  const [message, setMessage] = useState("");

  async function refresh() {
    setSubscriptions(await api<Subscription[]>("/subscriptions/mine"));
  }

  useEffect(() => { refresh().catch((error: unknown) => setMessage(error instanceof ApiError ? error.message : "Unable to load subscriptions")); }, []);

  async function setAutoRenew(subscription: Subscription, enabled: boolean) {
    setMessage("");
    try {
      await api(`/subscriptions/${subscription.id}/auto-renew`, {
        method: "PATCH",
        body: JSON.stringify({ enabled }),
      });
      await refresh();
      setMessage(enabled ? "Subscription reactivated." : "Subscription will remain active until the current period ends.");
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "Unable to update subscription");
    }
  }

  return <section className="card"><p className="eyebrow">SUBSCRIPTIONS</p><h1>Your subscriptions</h1>{subscriptions.length === 0 && <p>No subscriptions yet.</p>}<ul>{subscriptions.map((subscription) => <li className="card" key={subscription.id}><h2>{subscription.duration.replace("month_", "")} month{subscription.duration === "month_1" ? "" : "s"}</h2><p>Status: {subscription.status}</p><p>Current period ends: {subscription.current_period_end ? new Date(subscription.current_period_end).toLocaleDateString() : "Pending payment"}</p><p>{subscription.auto_renew ? "Renews automatically" : "Cancels at period end"}</p>{["active", "grace_period"].includes(subscription.status) && (subscription.auto_renew ? <button onClick={() => setAutoRenew(subscription, false)}>Cancel at period end</button> : <button onClick={() => setAutoRenew(subscription, true)}>Reactivate subscription</button>)}</li>)}</ul>{message && <p className="error">{message}</p>}</section>;
}
