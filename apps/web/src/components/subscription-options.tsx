"use client";

import { useEffect, useRef, useState } from "react";

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

export type SubscriptionAttemptKey = { duration: string; key: string };

export function subscriptionAttemptKey(
  current: SubscriptionAttemptKey | null,
  duration: string,
  createKey: () => string = () => crypto.randomUUID(),
): SubscriptionAttemptKey {
  return current?.duration === duration ? current : { duration, key: createKey() };
}

export function subscriptionPaymentRequiresNewKey(status: string): boolean {
  return status === "payment_failed";
}

function durationLabel(value: string) {
  const months = Number(value.replace("month_", ""));
  return Number.isFinite(months) ? `${months} month${months === 1 ? "" : "s"}` : value;
}

export function SubscriptionOptions({
  username,
  creatorId,
  onActivated,
}: {
  username: string;
  creatorId: string;
  onActivated?: () => void;
}) {
  const { requireLogin } = useLoginGate();
  const [options, setOptions] = useState<Option[]>([]);
  const [selected, setSelected] = useState<Option | null>(null);
  const [status, setStatus] = useState("");
  const [dialogError, setDialogError] = useState("");
  const [working, setWorking] = useState("");
  const dialog = useRef<HTMLDialogElement>(null);
  const attempt = useRef<SubscriptionAttemptKey | null>(null);

  useEffect(() => {
    api<Option[]>(`/creators/${encodeURIComponent(username)}/subscription-options`)
      .then(setOptions)
      .catch(() => setOptions([]));
  }, [username]);

  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    if (selected && !node.open) node.showModal();
    if (!selected && node.open) node.close();
  }, [selected]);

  function requestSubscription(option: Option) {
    if (!requireLogin({ nextPath: `/creator/${encodeURIComponent(username)}` })) return;
    setStatus("");
    setDialogError("");
    attempt.current = subscriptionAttemptKey(attempt.current, option.duration);
    setSelected(option);
  }

  async function subscribe(option: Option) {
    setWorking(option.duration);
    setStatus("");
    setDialogError("");
    attempt.current = subscriptionAttemptKey(attempt.current, option.duration);
    try {
      const started = await api<{ id: string; status: string }>(`/subscriptions/creator/${creatorId}`, {
        method: "POST",
        headers: { "Idempotency-Key": attempt.current.key },
        body: JSON.stringify({ duration: option.duration }),
      });
      if (subscriptionPaymentRequiresNewKey(started.status)) {
        attempt.current = null;
        setDialogError("The previous payment attempt failed. Confirm again to start a new, safely tracked attempt.");
        return;
      }
      if (process.env.NODE_ENV !== "production") {
        await api(`/subscriptions/${started.id}/complete-development`, { method: "POST" });
        setStatus(`${durationLabel(option.duration)} subscription is active.`);
        onActivated?.();
        window.dispatchEvent(new Event("fanbackstage:entitlements-changed"));
      } else {
        setStatus("Subscription payment started. Access activates after the configured provider confirms the charge.");
      }
      setSelected(null);
      attempt.current = null;
    } catch (caught) {
      setDialogError(caught instanceof ApiError ? caught.message : "Subscription could not be started. Retry safely.");
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
              <button disabled={working === option.duration} onClick={() => requestSubscription(option)} type="button">
                {working === option.duration ? "Starting…" : `Choose ${durationLabel(option.duration)}`}
              </button>
            </article>
          ))}
        </div>
      ) : <p className={styles.mutedLine}>Subscriptions are not available right now.</p>}
      {status && <p className={styles.inlineStatus} role="status">{status}</p>}
      <dialog
        aria-labelledby="subscription-confirm-title"
        className={styles.subscriptionConfirmDialog}
        onCancel={(event) => { event.preventDefault(); if (!working) setSelected(null); }}
        onClick={(event) => { if (event.target === event.currentTarget && !working) setSelected(null); }}
        ref={dialog}
      >
        {selected && (
          <div className={styles.subscriptionConfirmPanel}>
            <button aria-label="Close subscription confirmation" disabled={Boolean(working)} onClick={() => setSelected(null)} type="button">×</button>
            <p className={styles.subscriptionEyebrow}>CONFIRM MEMBERSHIP</p>
            <h2 id="subscription-confirm-title">{durationLabel(selected.duration)} membership</h2>
            <div className={styles.subscriptionCharge}>
              <span>Charged now</span>
              <strong>{formatMoney(selected.effective_amount_minor, selected.currency)}</strong>
            </div>
            {selected.discount_basis_points > 0 && (
              <p>You save {formatMoney(selected.base_amount_minor - selected.effective_amount_minor, selected.currency)} ({selected.discount_basis_points / 100}%) on this charge.</p>
            )}
            <p>Automatic renewal is on. The configured payment provider will charge the plan price for another {durationLabel(selected.duration)} at the end of each period, subject to any eligible renewal promotion then in effect. You can turn off renewal before the next charge.</p>
            {dialogError && <p className={styles.subscriptionDialogError} role="alert">{dialogError}</p>}
            <div className={styles.subscriptionConfirmActions}>
              <button disabled={Boolean(working)} onClick={() => setSelected(null)} type="button">Cancel</button>
              <button disabled={Boolean(working)} onClick={() => void subscribe(selected)} type="button">
                {working ? "Confirming…" : `Confirm ${formatMoney(selected.effective_amount_minor, selected.currency)}`}
              </button>
            </div>
          </div>
        )}
      </dialog>
    </section>
  );
}
