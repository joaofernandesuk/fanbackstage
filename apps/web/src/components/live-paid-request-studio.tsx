"use client";

import { FormEvent, useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";

type PaidRequestOption = {
  id: string;
  label: string;
  amount_minor: number;
  currency: string;
  enabled: boolean;
};

type PaidRequest = {
  id: string;
  request_label: string | null;
  request_message: string | null;
  gross_amount_minor: number;
  currency: string;
  expires_at: string | null;
};

function formatMoney(amountMinor: number, currency: string) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(
    amountMinor / 100,
  );
}

export function LivePaidRequestStudio() {
  const [options, setOptions] = useState<PaidRequestOption[]>([]);
  const [requests, setRequests] = useState<PaidRequest[]>([]);
  const [message, setMessage] = useState("");

  async function refresh() {
    const [nextOptions, nextRequests] = await Promise.all([
      api<PaidRequestOption[]>("/live/paid-request-options"),
      api<PaidRequest[]>("/live/paid-requests/mine/creator"),
    ]);
    setOptions(nextOptions);
    setRequests(nextRequests);
  }

  useEffect(() => {
    void refresh().catch(() => undefined);
    const interval = window.setInterval(() => void refresh().catch(() => undefined), 3_000);
    return () => window.clearInterval(interval);
  }, []);

  async function saveOption(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    try {
      await api("/live/paid-request-options", {
        method: "POST",
        body: JSON.stringify({
          label: values.get("paid-request-label"),
          amount_minor: Number(values.get("paid-request-amount")),
          enabled: true,
          sort_order: options.length,
          requires_creator_acceptance: true,
        }),
      });
      form.reset();
      await refresh();
      setMessage("Paid request option saved with a server-owned price.");
    } catch (caught) {
      setMessage(
        caught instanceof ApiError ? caught.message : "Unable to save paid request option",
      );
    }
  }

  async function resolve(request: PaidRequest, action: "accept" | "decline") {
    try {
      await api(`/live/paid-requests/${request.id}/${action}`, { method: "POST" });
      await refresh();
      setMessage(
        action === "accept"
          ? "Paid request accepted and settled once."
          : "Paid request declined. Its captured payment is queued for refund.",
      );
    } catch (caught) {
      setMessage(caught instanceof ApiError ? caught.message : "Unable to resolve paid request");
    }
  }

  return (
    <section className="card" aria-label="Live paid requests">
      <h2>Paid requests</h2>
      <p>Prices are resolved by the server and apply only to future requests.</p>
      <form aria-label="Paid request option" onSubmit={saveOption}>
        <label>
          Request label
          <input maxLength={100} name="paid-request-label" required />
        </label>
        <label>
          Price (minor units)
          <input min={1} name="paid-request-amount" required type="number" />
        </label>
        <button type="submit">Add paid request option</button>
      </form>
      {options.length > 0 && (
        <ul aria-label="Paid request menu">
          {options.map((option) => (
            <li key={option.id}>
              {option.label} · {formatMoney(option.amount_minor, option.currency)}
            </li>
          ))}
        </ul>
      )}
      {requests.length > 0 && (
        <section aria-label="Paid requests awaiting your decision">
          <h3>Awaiting your decision</h3>
          <ul>
            {requests.map((request) => (
              <li key={request.id}>
                <strong>{request.request_label}</strong> ·{" "}
                {formatMoney(request.gross_amount_minor, request.currency)}
                <p>{request.request_message}</p>
                <button onClick={() => void resolve(request, "accept")} type="button">
                  Accept paid request
                </button>
                <button onClick={() => void resolve(request, "decline")} type="button">
                  Decline paid request
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
