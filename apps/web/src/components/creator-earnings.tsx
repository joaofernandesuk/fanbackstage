"use client";

import { useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";

type Earnings = { pending_amount_minor: number; available_amount_minor: number; ppv_gross_amount_minor: number; platform_fee_amount_minor: number; creator_net_amount_minor: number; currency: string };

export function CreatorEarnings() {
  const [earnings, setEarnings] = useState<Earnings | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api<Earnings>("/finance/creator/earnings?currency=EUR").then(setEarnings).catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Unable to load earnings")); }, []);
  if (error) return <p className="error">{error}</p>;
  if (!earnings) return <p>Loading financial summary…</p>;
  return <section><h2>Financial summary</h2><p>Pending: {earnings.pending_amount_minor} {earnings.currency}</p><p>Available: {earnings.available_amount_minor} {earnings.currency}</p><p>PPV gross: {earnings.ppv_gross_amount_minor} {earnings.currency}</p><p>Platform fees: {earnings.platform_fee_amount_minor} {earnings.currency}</p><p>Creator net: {earnings.creator_net_amount_minor} {earnings.currency}</p></section>;
}
