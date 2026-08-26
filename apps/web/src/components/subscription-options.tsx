"use client";

import { useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";
import { formatMoney } from "../lib/public-api";
import { useLoginGate } from "./consumer-ui";
import styles from "./social-surface.module.css";

type Option = {
  duration: string;
  base_amount_minor: number;
  effective_amount_minor: number;
  currency: string;
  discount_basis_points: number;
};

function durationLabel(value: string) {
  const months = Number(value.replace("month_", ""));
  return Number.isFinite(months) ? `${months} month${months === 1 ? "" : "s"}` : value;
}

export function SubscriptionOptions({ username, creatorId }: { username: string; creatorId: string }) {
  const { requireLogin } = useLoginGate();
  const [options, setOptions] = useState<Option[]>([]);
  const [status, setStatus] = useState("");
  const [working, setWorking] = useState("");

  useEffect(() => {
    api<Option[]>(`/creators/${encodeURIComponent(username)}/subscription-options`)
      .then(setOptions)
      .catch(() => setOptions([]));
  }, [username]);

  async function subscribe(duration: string) {
    if (!requireLogin()) return;
    setWorking(duration);
    setStatus("");
    try {
      const started = await api<{ id: string }>(`/subscriptions/creator/${creatorId}`, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({ duration }),
      });
      await api(`/subscriptions/${started.id}/complete-development`, { method: "POST" });
      setStatus("Subscription is active.");
    } catch (caught) {
      setStatus(caught instanceof ApiError ? caught.message : "Subscription could not be started");
    } finally {
      setWorking("");
    }
  }

  return (
    <section aria-label="Subscriptions">
      <h2>Join the inner circle</h2>
      <p>Unlock subscriber posts and support this creator directly.</p>
      {options.length ? (
        <div className={styles.subscriptionList}>
          {options.map((option) => (
            <article className={styles.subscriptionOption} key={option.duration}>
              <div>
                <strong>{durationLabel(option.duration)}</strong>
                {option.discount_basis_points > 0 && <span>Save {option.discount_basis_points / 100}%</span>}
              </div>
              <p>
                <b>{formatMoney(option.effective_amount_minor, option.currency)}</b>
                {option.discount_basis_points > 0 && <s>{formatMoney(option.base_amount_minor, option.currency)}</s>}
              </p>
              <button disabled={working === option.duration} onClick={() => void subscribe(option.duration)} type="button">
                {working === option.duration ? "Starting…" : "Subscribe"}
              </button>
            </article>
          ))}
        </div>
      ) : <p className={styles.mutedLine}>Subscriptions are not available right now.</p>}
      {status && <p className={styles.inlineStatus} role="status">{status}</p>}
    </section>
  );
}
