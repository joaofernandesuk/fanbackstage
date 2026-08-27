"use client";

import { useState } from "react";

import { api, ApiError } from "../lib/api";
import styles from "./adult-access-gate.module.css";

type AdultAccessStatus = {
  allowed: boolean;
  assurance: "none" | "self_attested";
  source: "none" | "account" | "cookie";
  policy_version: string;
  expires_at: string | null;
};

export function AdultAccessGate({
  onGranted,
  title,
}: {
  onGranted: () => Promise<void>;
  title: string;
}) {
  const [confirmed, setConfirmed] = useState(false);
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");

  async function acknowledge() {
    if (!confirmed) return;
    setWorking(true);
    setError("");
    try {
      const status = await api<AdultAccessStatus>("/auth/adult-access", {
        method: "POST",
        body: JSON.stringify({ adult_confirmed: true }),
      });
      if (!status.allowed) throw new Error("Adult access could not be confirmed");
      await onGranted();
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 429
          ? "Too many attempts. Wait a moment, then try again."
          : "Adult access could not be confirmed. Try again.",
      );
    } finally {
      setWorking(false);
    }
  }

  return (
    <section aria-labelledby="adult-access-title" className={styles.gate}>
      <div aria-hidden="true" className={styles.mark}>18+</div>
      <p className={styles.eyebrow}>ADULTS ONLY</p>
      <h2 id="adult-access-title">Confirm your age to view {title}</h2>
      <p>This release contains age-restricted media. Access is controlled by the server and does not grant a purchase or subscription entitlement.</p>
      <label className={styles.confirmation}>
        <input checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} type="checkbox" />
        <span>I confirm that I am at least 18 years old.</span>
      </label>
      <button disabled={!confirmed || working} onClick={() => void acknowledge()} type="button">
        {working ? "Confirming…" : "Confirm and continue"}
      </button>
      {error && <p className={styles.error} role="alert">{error}</p>}
    </section>
  );
}
