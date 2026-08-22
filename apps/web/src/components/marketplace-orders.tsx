"use client";

import { useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";

type Order = {
  id: string;
  public_id: string;
  listing_id: string;
  status: string;
  quantity: number;
  currency: string;
  item_subtotal_minor: number;
  shipping_charged_minor: number;
  shipping_allowance_minor: number;
  shipping_pass_through_minor: number;
  shipping_excess_minor: number;
  commissionable_base_minor: number;
  total_paid_minor: number;
  carrier?: string | null;
  tracking_reference?: string | null;
  shipped_at?: string | null;
  delivered_at?: string | null;
  earnings_hold_until?: string | null;
};
type Tracking = { event_type: string; carrier?: string | null; tracking_reference?: string | null; created_at: string };

export function MarketplaceOrders() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [tracking, setTracking] = useState<Record<string, Tracking[]>>({});
  const [error, setError] = useState("");
  useEffect(() => { api<Order[]>("/marketplace/orders/mine").then(setOrders).catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Unable to load marketplace orders")); }, []);
  async function showTracking(orderId: string) {
    try { const rows = await api<Tracking[]>(`/marketplace/orders/${orderId}/tracking`); setTracking((current) => ({ ...current, [orderId]: rows })); } catch (e) { setError(e instanceof ApiError ? e.message : "Unable to load tracking history"); }
  }
  return <section className="card marketplace-card"><p className="eyebrow">MARKETPLACE ORDERS</p><h1>Your orders</h1>{error && <p className="error">{error}</p>}<ul className="marketplace-list">{orders.map((order) => <li key={order.id}><strong>{order.public_id}</strong><p>{order.status} · {order.quantity} item{order.quantity === 1 ? "" : "s"} · {order.total_paid_minor} {order.currency}</p><dl><dt>Items</dt><dd>{order.item_subtotal_minor} {order.currency}</dd><dt>Shipping charged</dt><dd>{order.shipping_charged_minor} {order.currency}</dd><dt>Tracking</dt><dd>{order.carrier ?? "Not shipped"}{order.tracking_reference ? ` · ${order.tracking_reference}` : ""}</dd></dl><button type="button" onClick={() => showTracking(order.id)}>View tracking history</button>{tracking[order.id] && <ul>{tracking[order.id].map((event) => <li key={`${event.event_type}-${event.created_at}`}>{event.event_type}: {event.carrier ?? ""} {event.tracking_reference ?? ""}</li>)}</ul>}</li>)}</ul>{!error && !orders.length && <p>No marketplace orders yet.</p>}</section>;
}
