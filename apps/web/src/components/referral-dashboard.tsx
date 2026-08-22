"use client";

import { useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";

type Totals = Record<string, {
  pending_amount_minor: number;
  available_amount_minor: number;
  reversed_amount_minor: number;
}>;
type Allocation = {
  id: string;
  revenue_type: string;
  currency: string;
  amount_minor: number;
  allocated_at: string;
  released_at: string | null;
  reversed_at: string | null;
};
type Link = {
  public_id: string;
  code: string;
  destination_path: string;
  status: string;
  conversions: number;
};
type Dashboard = { totals_by_currency: Totals; allocations: Allocation[]; links: Link[] };

export function ReferralDashboard({ heading = "Referral earnings" }: { heading?: string }) {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Dashboard>("/r/me/dashboard")
      .then(setDashboard)
      .catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Unable to load referral earnings"));
  }, []);

  return <section>
    <h2>{heading}</h2>
    <p>Totals are derived from immutable referral allocations and their ledger release or reversal events.</p>
    {dashboard && <>
      {Object.entries(dashboard.totals_by_currency).map(([currency, totals]) => <section key={currency}>
        <h3>{currency}</h3>
        <p>Pending: {totals.pending_amount_minor} {currency}</p>
        <p>Available: {totals.available_amount_minor} {currency}</p>
        <p>Reversed: {totals.reversed_amount_minor} {currency}</p>
      </section>)}
      {!Object.keys(dashboard.totals_by_currency).length && <p>No referral earnings yet.</p>}
      <h3>Your links and conversions</h3>
      <ul>{dashboard.links.map((link) => <li key={link.public_id}>
        <code>/r/{link.code}</code> → {link.destination_path} ({link.status}); conversions: {link.conversions}
      </li>)}</ul>
      <h3>Allocation history</h3>
      <ul>{dashboard.allocations.map((allocation) => <li key={allocation.id}>
        {allocation.revenue_type}: {allocation.amount_minor} {allocation.currency} — {
          allocation.reversed_at ? "reversed" : allocation.released_at ? "available" : "pending"
        }
      </li>)}</ul>
    </>}
    {error && <p className="error">{error}</p>}
  </section>;
}
