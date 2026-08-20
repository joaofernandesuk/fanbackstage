"use client";

import { useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";

type Purchase = { id: string; content_title: string; creator_username: string; gross_amount_minor: number; currency: string; status: string };

export function PurchaseHistory() {
  const [purchases, setPurchases] = useState<Purchase[]>([]);
  const [error, setError] = useState("");
  useEffect(() => { api<Purchase[]>("/purchases/mine").then(setPurchases).catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Unable to load purchases")); }, []);
  return <section className="card"><p className="eyebrow">PURCHASE HISTORY</p><h1>Your unlocks</h1>{error && <p className="error">{error}</p>}<ul>{purchases.map((purchase) => <li key={purchase.id}>{purchase.content_title} by @{purchase.creator_username} — {purchase.gross_amount_minor} {purchase.currency} ({purchase.status})</li>)}</ul>{!error && purchases.length === 0 && <p>No purchases yet.</p>}</section>;
}
