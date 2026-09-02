"use client";

import { FormEvent, useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";
import { completePaymentCheckout } from "../lib/payments";
import { formatMoney } from "../lib/public-api";

type SendPrice = {
  amount_minor: number | null;
  currency: string | null;
  requires_confirmation: boolean;
};

export function CreatorMessageComposer({ creatorId }: { creatorId: string }) {
  const [price, setPrice] = useState<SendPrice | null>(null);
  const [body, setBody] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    api<SendPrice>(`/messages/creator/${creatorId}/send-price`)
      .then(setPrice)
      .catch((requestError: unknown) => {
        setError(requestError instanceof ApiError ? requestError.message : "Unable to load messaging options");
      });
  }, [creatorId]);

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!body.trim() || !price) return;
    setError("");
    if (price.requires_confirmation && !confirming) {
      setConfirming(true);
      return;
    }
    try {
      if (price.requires_confirmation) {
        const payment = await api<{ payment_attempt_id: string }>(`/messages/creator/${creatorId}/paid-send`, {
          method: "POST",
          headers: { "Idempotency-Key": crypto.randomUUID() },
          body: JSON.stringify({ body }),
        });
        setMessage(
          await completePaymentCheckout(payment.payment_attempt_id)
            ? "Your paid message was delivered."
            : "Payment started. Your message is delivered only after provider confirmation.",
        );
      } else {
        await api(`/messages/creator/${creatorId}`, { method: "POST", body: JSON.stringify({ body }) });
        setMessage("Your message was delivered.");
      }
      setBody("");
      setConfirming(false);
    } catch (requestError) {
      setError(requestError instanceof ApiError ? requestError.message : "Message could not be sent");
    }
  }

  return (
    <section aria-label="Message creator">
      <h2>Message creator</h2>
      {price && (
        <p>
          {price.requires_confirmation
            ? `This creator charges ${formatMoney(price.amount_minor ?? 0, price.currency ?? "EUR")} to send a message.`
            : "This creator accepts messages without a send fee."}
        </p>
      )}
      <form onSubmit={send}>
        <label>
          Message
          <textarea value={body} onChange={(event) => setBody(event.target.value)} maxLength={4000} required />
        </label>
        {confirming && price?.requires_confirmation && (
          <p role="alert">Confirm payment of {formatMoney(price.amount_minor ?? 0, price.currency ?? "EUR")} to deliver this message.</p>
        )}
        <button disabled={!price}>{confirming ? "Confirm and pay" : "Send message"}</button>
      </form>
      {message && <p>{message}</p>}
      {error && <p className="error">{error}</p>}
    </section>
  );
}
