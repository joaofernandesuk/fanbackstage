"use client";

import { FormEvent, useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";

type Order = { id: string; public_id: string; status: string; quantity: number; currency: string; total_paid_minor: number; carrier?: string | null; tracking_reference?: string | null; earnings_hold_until?: string | null };

export function MarketplaceFulfilment() {
  const [orders, setOrders] = useState<Order[]>([]);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const load = async () => { try { setOrders(await api<Order[]>("/marketplace/orders/fulfilment")); } catch (e) { setError(e instanceof ApiError ? e.message : "Unable to load fulfilment orders"); } };
  useEffect(() => { void load(); }, []);
  async function processing(orderId: string) { try { await api(`/marketplace/orders/${orderId}/processing`, { method: "POST" }); setNotice("Order moved to processing."); await load(); } catch (e) { setError(e instanceof ApiError ? e.message : "Order could not be updated"); } }
  async function shipped(event: FormEvent<HTMLFormElement>, orderId: string) { event.preventDefault(); const form = new FormData(event.currentTarget); try { await api(`/marketplace/orders/${orderId}/shipped`, { method: "POST", body: JSON.stringify({ carrier: form.get("carrier") || undefined, tracking_reference: form.get("tracking") || undefined }) }); setNotice("Immutable shipment record added."); await load(); } catch (e) { setError(e instanceof ApiError ? e.message : "Shipment could not be recorded"); } }
  async function cancel(orderId: string) { try { await api(`/marketplace/orders/${orderId}/cancel`, { method: "POST", body: JSON.stringify({ reason: "Seller cancellation" }) }); setNotice("Unshipped order cancelled and refunded."); await load(); } catch (e) { setError(e instanceof ApiError ? e.message : "Order could not be cancelled"); } }
  return <section><h2>Marketplace fulfilment</h2><p>Orders are fulfilled only after verified payment. Shipment records are immutable.</p>{notice && <p>{notice}</p>}{error && <p className="error">{error}</p>}<ul className="marketplace-list">{orders.map((order) => <li key={order.id}><strong>{order.public_id}</strong><p>{order.status} · {order.quantity} item{order.quantity === 1 ? "" : "s"} · {order.total_paid_minor} {order.currency}</p>{order.earnings_hold_until && <p>Marketplace earnings hold until {new Date(order.earnings_hold_until).toLocaleString()}.</p>}{order.status === "paid" && <button type="button" onClick={() => processing(order.id)}>Start processing</button>}{(order.status === "paid" || order.status === "processing") && <><form onSubmit={(event) => shipped(event, order.id)}><label>Carrier<input name="carrier" maxLength={120} /></label><label>Tracking reference<input name="tracking" maxLength={255} /></label><button>Record shipment</button></form><button type="button" onClick={() => cancel(order.id)}>Cancel and refund</button></>}{order.tracking_reference && <p>{order.carrier ?? "Carrier"}: {order.tracking_reference}</p>}</li>)}</ul>{!orders.length && <p>No marketplace orders to fulfil.</p>}</section>;
}
