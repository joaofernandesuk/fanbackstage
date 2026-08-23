"use client";

import { FormEvent, useEffect, useState } from "react";

import { ApiError, api } from "../../../lib/api";

type Surface = { id: string; kind: string; slots: { id: string; slot_key: string }[] };
type Booking = { public_id: string; status: string; price_minor: number; currency: string };

export default function FeaturingAdminPage() {
  const [surfaces, setSurfaces] = useState<Surface[]>([]), [bookings, setBookings] = useState<Booking[]>([]), [message, setMessage] = useState("");
  const refresh = async () => { const [nextSurfaces, nextBookings] = await Promise.all([api<Surface[]>("/featuring/inventory"), api<Booking[]>("/featuring/admin/bookings")]); setSurfaces(nextSurfaces); setBookings(nextBookings); };
  useEffect(() => { void refresh().catch((error: unknown) => setMessage(error instanceof ApiError ? error.message : "Unable to load featuring controls")); }, []);
  async function submit(path: string, event: FormEvent<HTMLFormElement>) { event.preventDefault(); const form = new FormData(event.currentTarget); const body = Object.fromEntries([...form].map(([key, value]) => [key, ["position", "capacity", "duration_seconds", "amount_minor", "cancellation_cutoff_seconds"].includes(key) ? Number(value) : value])); try { await api(path, { method: "POST", body: JSON.stringify(body) }); await refresh(); setMessage("Saved server-authoritative featuring configuration."); } catch (error) { setMessage(error instanceof ApiError ? error.message : "Unable to save configuration"); } }
  return <section className="card"><p className="eyebrow">ADMIN</p><h1>Featuring inventory</h1><p>Sponsored inventory is fixed-price and separate from organic ranking.</p><form onSubmit={(event) => void submit("/featuring/admin/surfaces", event)}><input name="kind" placeholder="discover_home_hero" required /><input name="cancellation_cutoff_seconds" type="number" defaultValue="3600" /><button>Create surface</button></form><form onSubmit={(event) => void submit("/featuring/admin/slots", event)}><select name="surface_id">{surfaces.map(surface => <option key={surface.id} value={surface.id}>{surface.kind}</option>)}</select><input name="slot_key" placeholder="hero-1" required /><input name="position" type="number" defaultValue="0" /><input name="capacity" type="number" defaultValue="1" /><button>Create slot</button></form><form onSubmit={(event) => void submit("/featuring/admin/prices", event)}><select name="slot_id">{surfaces.flatMap(surface => surface.slots.map(slot => <option key={slot.id} value={slot.id}>{surface.kind}/{slot.slot_key}</option>))}</select><input name="target_type" defaultValue="creator" /><input name="duration_seconds" type="number" defaultValue="3600" /><input name="amount_minor" type="number" min="1" required /><input name="currency" defaultValue="EUR" maxLength={3} /><button>Create price version</button></form><h2>Booking history</h2><ul>{bookings.map(booking => <li key={booking.public_id}>{booking.public_id} · {booking.status} · {(booking.price_minor / 100).toFixed(2)} {booking.currency}</li>)}</ul>{message && <p role="status">{message}</p>}</section>;
}
