"use client";

import { FormEvent, useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";
import styles from "./operations-workspace.module.css";

type PrivatePeekPolicy = {
  id: string;
  active: boolean;
  amount_minor: number;
  currency: string;
  commission_basis_points: number;
  updated_at: string;
};

export function LivePrivatePeekPolicy() {
  const [policy, setPolicy] = useState<PrivatePeekPolicy | null>(null);
  const [message, setMessage] = useState("");

  useEffect(() => {
    void api<PrivatePeekPolicy>("/live/admin/private-peek-policy")
      .then(setPolicy)
      .catch((caught) =>
        setMessage(
          caught instanceof ApiError
            ? caught.message
            : "Unable to load the private-peek policy.",
        ),
      );
  }, []);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    try {
      const updated = await api<PrivatePeekPolicy>(
        "/live/admin/private-peek-policy",
        {
          method: "PUT",
          body: JSON.stringify({
            active: values.get("active") === "on",
            amount_minor: Number(values.get("amount_minor")),
            currency: values.get("currency"),
            commission_basis_points: Number(
              values.get("commission_basis_points"),
            ),
            reason: values.get("reason"),
            confirmed: values.get("confirmed") === "on",
          }),
        },
      );
      setPolicy(updated);
      setMessage(
        "Private-peek policy saved. Existing private sessions keep their original snapshotted terms.",
      );
      event.currentTarget.reset();
    } catch (caught) {
      setMessage(
        caught instanceof ApiError
          ? caught.message
          : "Unable to save the private-peek policy.",
      );
    }
  }

  if (!policy) return message ? <p role="status">{message}</p> : null;

  return (
    <section className={styles.panel} aria-label="Private Live peek policy">
      <p className="eyebrow">PRIVATE LIVE</p>
      <h2>Paid peek policy</h2>
      <p>
        Administrators own the global price and commission. Creators may only
        allow or disallow peeks; accepted sessions retain these exact terms.
      </p>
      <form className={styles.filters} key={policy.updated_at} onSubmit={save}>
        <label className={styles.confirm}>
          <input defaultChecked={policy.active} name="active" type="checkbox" />
          Enable paid peeks platform-wide
        </label>
        <label>
          Price (minor units)
          <input defaultValue={policy.amount_minor} min="1" name="amount_minor" required type="number" />
        </label>
        <label>
          Currency
          <input defaultValue={policy.currency} maxLength={3} minLength={3} name="currency" required />
        </label>
        <label>
          Platform commission (basis points)
          <input defaultValue={policy.commission_basis_points} max="10000" min="0" name="commission_basis_points" required type="number" />
        </label>
        <label>
          Audit reason
          <input minLength={8} name="reason" required />
        </label>
        <label className={styles.confirm}>
          <input name="confirmed" required type="checkbox" />
          I confirm this financial policy change
        </label>
        <button>Save paid-peek policy</button>
      </form>
      {message && <p role="status">{message}</p>}
    </section>
  );
}
