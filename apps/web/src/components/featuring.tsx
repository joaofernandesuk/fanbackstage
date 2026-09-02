"use client";

import { FormEvent, useEffect, useRef, useState } from "react";

import { ApiError, api } from "../lib/api";
import { completePaymentCheckout } from "../lib/payments";
import styles from "./featuring.module.css";

type Price = {
  id: string;
  target_type: string;
  duration_seconds: number;
  amount_minor: number;
  currency: string;
  version: number;
};
type Slot = { id: string; slot_key: string; position: number; active: boolean; prices: Price[] };
type Surface = { id: string; kind: string; status: string; slots: Slot[] };
type Target = { target_type: string; target_id: string; title: string };
type Booking = {
  id: string;
  status: string;
  starts_at: string;
  ends_at: string;
  price_minor: number;
  currency: string;
  payment_attempt_id: string | null;
  purchaser_user_id: string;
  actor_user_id: string;
  reservation_expires_at: string | null;
  retryable: boolean;
};
type PaymentResult = {
  payment_attempt_id: string;
  status: string;
  booking_status: string;
};
export type FeaturingAttemptKey = { bookingId: string; key: string };

function formatMoney(amountMinor: number, currency: string) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(amountMinor / 100);
}

export function featuringAttemptKey(
  current: FeaturingAttemptKey | null,
  bookingId: string,
  forceNew = false,
  generate: () => string = () => crypto.randomUUID(),
) {
  if (!forceNew && current?.bookingId === bookingId) return current;
  return { bookingId, key: generate() };
}

export function featuringPaymentError(caught: unknown) {
  if (caught instanceof ApiError) {
    if (caught.status === 401) return "Log in again before authorizing this payment.";
    if (caught.status === 403) return "Only the selected payer can authorize this booking.";
    if (caught.status === 404) return "This booking or test payment is no longer available.";
    if (caught.status === 409) return "Payment state changed. Refresh the booking before retrying.";
    if (caught.status === 429) return "Too many payment attempts. Wait a moment before retrying.";
    if (caught.status === 400 && caught.message.includes("reservation has expired")) {
      return "This slot reservation expired before payment completed. Create a new booking.";
    }
    if (caught.status === 400 && caught.message.includes("adult self-attestation")) {
      return "Confirm the current 18+ access notice before starting payment.";
    }
  }
  return "Payment could not be confirmed. Your booking history remains visible; retry safely from its current status.";
}

export function featuringPaymentAction(booking: Pick<Booking, "status" | "retryable">) {
  if (booking.status === "failed" && booking.retryable) return "Retry payment";
  if (booking.status === "awaiting_payment" && booking.retryable) return "Review and authorize payment";
  return null;
}

export function FeaturingDashboard({ manager = false }: { manager?: boolean }) {
  const [inventory, setInventory] = useState<Surface[]>([]);
  const [targets, setTargets] = useState<Target[]>([]);
  const [bookings, setBookings] = useState<Booking[]>([]);
  const [message, setMessage] = useState("");
  const [dialogError, setDialogError] = useState("");
  const [selectedBooking, setSelectedBooking] = useState<Booking | null>(null);
  const [working, setWorking] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null);
  const paymentKey = useRef<FeaturingAttemptKey | null>(null);

  const refresh = async () => {
    const [surfaces, availableTargets, history] = await Promise.all([
      api<Surface[]>("/featuring/inventory"),
      api<Target[]>("/featuring/eligible-targets"),
      api<Booking[]>("/featuring/bookings/mine"),
    ]);
    setInventory(surfaces);
    setTargets(availableTargets);
    setBookings(history);
  };

  useEffect(() => {
    void refresh().catch(() => setMessage("Featuring could not be loaded for this account."));
  }, []);

  useEffect(() => {
    const node = dialog.current;
    if (!node) return;
    if (selectedBooking && !node.open) node.showModal();
    if (!selectedBooking && node.open) node.close();
  }, [selectedBooking]);

  async function book(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const row = await api<Booking>("/featuring/bookings", {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({
          slot_id: form.get("slot_id"),
          target_type: form.get("target_type"),
          target_id: form.get("target_id"),
          starts_at: new Date(String(form.get("starts_at"))).toISOString(),
          duration_seconds: Number(form.get("duration_seconds")),
          ...(manager && form.get("payer_user_id") ? { payer_user_id: form.get("payer_user_id") } : {}),
        }),
      });
      setMessage(`Booking reserved at the server price. The selected payer must review ${formatMoney(row.price_minor, row.currency)} before payment.`);
      await refresh();
    } catch {
      setMessage("The booking could not be reserved. Review its target, time, and availability.");
    }
  }

  function reviewPayment(booking: Booking) {
    paymentKey.current = featuringAttemptKey(
      paymentKey.current,
      booking.id,
      booking.status === "failed",
    );
    setDialogError("");
    setSelectedBooking(booking);
  }

  async function confirmPayment(booking: Booking) {
    setWorking(true);
    setDialogError("");
    setMessage("");
    const attemptKey = featuringAttemptKey(paymentKey.current, booking.id);
    paymentKey.current = attemptKey;
    try {
      const result = await api<PaymentResult>(`/featuring/bookings/${booking.id}/payment`, {
        method: "POST",
        headers: { "Idempotency-Key": attemptKey.key },
      });
      if (result.status === "failed") {
        paymentKey.current = null;
        setSelectedBooking(null);
        setMessage("The previous provider attempt failed. Your reservation is still visible; choose Retry payment to make a new deliberate attempt.");
        await refresh();
        return;
      }
      if (await completePaymentCheckout(result.payment_attempt_id)) {
        setMessage("Test payment confirmed. One server settlement now owns this booking.");
      } else {
        setMessage("Payment authorization started. The booking remains pending until the configured provider confirms the charge.");
      }
      paymentKey.current = null;
      setSelectedBooking(null);
      await refresh();
    } catch (caught) {
      setDialogError(featuringPaymentError(caught));
    } finally {
      setWorking(false);
    }
  }

  const prices = inventory.flatMap((surface) => surface.slots.flatMap((slot) => (
    slot.prices.map((price) => ({ surface, slot, price }))
  )));

  return (
    <section className={`card ${styles.dashboard}`}>
      <p className="eyebrow">{manager ? "MANAGER FEATURING" : "CREATOR FEATURING"}</p>
      <h2>Feature a public target</h2>
      <p>Sponsored placements are visibly labelled and remain separate from organic discovery.</p>
      <form className={styles.bookingForm} onSubmit={book}>
        <label>
          Target
          <select name="target_id" required>
            {targets.map((target) => <option key={target.target_id} value={target.target_id}>{target.title} ({target.target_type})</option>)}
          </select>
        </label>
        <input name="target_type" type="hidden" value={targets[0]?.target_type ?? "creator"} />
        <label>
          Inventory / server price
          <select name="slot_id" required>
            {prices.map(({ surface, slot, price }) => (
              <option key={price.id} value={slot.id}>{surface.kind} · {slot.slot_key} · {formatMoney(price.amount_minor, price.currency)} / {price.duration_seconds / 3600}h</option>
            ))}
          </select>
        </label>
        <label>Duration (seconds; must match an offered product)<input defaultValue={prices[0]?.price.duration_seconds ?? 3600} min="1" name="duration_seconds" required type="number" /></label>
        <label>Start (UTC)<input name="starts_at" required type="datetime-local" /></label>
        {manager && <label>Explicit payer user ID (leave blank to pay as manager)<input name="payer_user_id" placeholder="Creator must authorize if selected" /></label>}
        <button disabled={!targets.length || !prices.length}>Reserve slot</button>
      </form>

      <h2>Booking history</h2>
      {bookings.length ? (
        <ul className={styles.bookingList}>
          {bookings.map((booking) => {
            const action = featuringPaymentAction(booking);
            return (
              <li key={booking.id}>
                <div>
                  <strong>{booking.status.replaceAll("_", " ")}</strong>
                  <span>{formatMoney(booking.price_minor, booking.currency)} · {new Date(booking.starts_at).toLocaleString()}</span>
                  {["awaiting_payment", "failed"].includes(booking.status) && !booking.retryable && <small>Reservation expired. Create a new booking to try again.</small>}
                </div>
                {action && <button onClick={() => reviewPayment(booking)} type="button">{action}</button>}
              </li>
            );
          })}
        </ul>
      ) : <p>No featuring bookings yet.</p>}
      {message && <p className={styles.inlineStatus} role="status">{message}</p>}

      <dialog
        aria-labelledby="featuring-payment-title"
        className={styles.paymentDialog}
        onCancel={(event) => { event.preventDefault(); if (!working) setSelectedBooking(null); }}
        onClick={(event) => { if (event.target === event.currentTarget && !working) setSelectedBooking(null); }}
        ref={dialog}
      >
        {selectedBooking && (
          <div className={styles.paymentPanel}>
            <button aria-label="Close featuring payment confirmation" disabled={working} onClick={() => setSelectedBooking(null)} type="button">×</button>
            <p className="eyebrow">CONFIRM SPONSORED PLACEMENT</p>
            <h2 id="featuring-payment-title">Review featuring payment</h2>
            <dl>
              <div><dt>Charge</dt><dd>{formatMoney(selectedBooking.price_minor, selectedBooking.currency)}</dd></div>
              <div><dt>Starts</dt><dd>{new Date(selectedBooking.starts_at).toLocaleString()}</dd></div>
              <div><dt>Ends</dt><dd>{new Date(selectedBooking.ends_at).toLocaleString()}</dd></div>
            </dl>
            <p>The server keeps the original slot, window, price version, amount, and currency unchanged across a safe retry. A failed attempt does not count as payment.</p>
            <p>{process.env.NODE_ENV === "production" ? "The configured provider must confirm the charge before the booking is scheduled." : "This local confirmation uses the development provider and settles through the same signed webhook path."}</p>
            {dialogError && <p className={styles.dialogError} role="alert">{dialogError}</p>}
            <div className={styles.dialogActions}>
              <button disabled={working} onClick={() => setSelectedBooking(null)} type="button">Cancel</button>
              <button disabled={working} onClick={() => void confirmPayment(selectedBooking)} type="button">
                {working ? "Confirming…" : process.env.NODE_ENV === "production" ? `Authorize ${formatMoney(selectedBooking.price_minor, selectedBooking.currency)}` : `Confirm test payment of ${formatMoney(selectedBooking.price_minor, selectedBooking.currency)}`}
              </button>
            </div>
          </div>
        )}
      </dialog>
    </section>
  );
}
