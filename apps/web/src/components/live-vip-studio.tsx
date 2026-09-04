"use client";

import { type FormEvent, useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";

type LiveRoom = { id: string; status: string };
type LiveVipShow = {
  id: string;
  status: "preshow" | "awaiting_creator" | "active" | "completed" | "cancelled";
  title: string;
  description: string;
  goal_amount_minor: number;
  confirmed_amount_minor: number;
  buy_in_amount_minor: number;
  currency: string;
  preshow_ends_at: string;
  duration_seconds: number;
  started_at: string | null;
  ends_at: string | null;
};

function money(amountMinor: number, currency: string) {
  return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(amountMinor / 100);
}

function remaining(until: string | null, now: number) {
  if (!until) return "";
  const seconds = Math.max(0, Math.ceil((new Date(until).getTime() - now) / 1000));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function LiveVipStudio() {
  const [room, setRoom] = useState<LiveRoom | null>(null);
  const [show, setShow] = useState<LiveVipShow | null>(null);
  const [message, setMessage] = useState("");
  const [pending, setPending] = useState(false);
  const [now, setNow] = useState(Date.now());

  useEffect(() => {
    let cancelled = false;
    let pollTimer: number | undefined;
    const poll = async () => {
      try {
        const currentRoom = await api<LiveRoom | null>("/live/rooms/mine");
        if (cancelled) return;
        setRoom(currentRoom);
        if (currentRoom?.status === "live") {
          setShow(await api<LiveVipShow | null>(`/live/rooms/${currentRoom.id}/vip-show`));
        } else {
          setShow(null);
        }
      } catch {
        // The next bounded poll recovers creator state without touching media.
      } finally {
        if (!cancelled) pollTimer = window.setTimeout(() => void poll(), 2_000);
      }
    };
    void poll();
    const clock = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => {
      cancelled = true;
      if (pollTimer !== undefined) window.clearTimeout(pollTimer);
      window.clearInterval(clock);
    };
  }, []);

  async function createShow(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!room || pending) return;
    const values = new FormData(event.currentTarget);
    setPending(true);
    setMessage("");
    try {
      setShow(await api<LiveVipShow>(`/live/rooms/${room.id}/vip-show`, {
        method: "POST",
        body: JSON.stringify({
          title: values.get("vip-title"),
          description: values.get("vip-description"),
          goal_amount_minor: Math.round(Number(values.get("vip-goal")) * 100),
          buy_in_amount_minor: Math.round(Number(values.get("vip-buy-in")) * 100),
          preshow_minutes: Number(values.get("vip-preshow")),
          duration_minutes: Number(values.get("vip-duration")),
        }),
      }));
      setMessage("VIP pre-show started. The promise, goal, buy-in, and timing are now locked.");
    } catch (caught) {
      setMessage(caught instanceof ApiError ? caught.message : "Unable to start the VIP pre-show");
    } finally {
      setPending(false);
    }
  }

  async function control(action: "start" | "cancel") {
    if (!room || pending) return;
    setPending(true);
    setMessage("");
    try {
      setShow(await api<LiveVipShow>(`/live/rooms/${room.id}/vip-show/${action}`, { method: "POST" }));
      setMessage(action === "start"
        ? "VIP show started. Non-admitted viewers are being removed through the durable Live control queue."
        : "VIP show cancelled. Confirmed captures were placed in the refund workflow.");
    } catch (caught) {
      setMessage(caught instanceof ApiError ? caught.message : `Unable to ${action} the VIP show`);
    } finally {
      setPending(false);
    }
  }

  const progress = show ? Math.min(100, Math.round(show.confirmed_amount_minor / show.goal_amount_minor * 100)) : 0;

  return (
    <section aria-label="VIP show controls" className="card">
      <p className="eyebrow">PAID GROUP SHOW</p>
      <h2>VIP mode</h2>
      {!room || room.status !== "live" ? (
        <p>Start your public Live room before scheduling its VIP segment.</p>
      ) : !show ? (
        <form onSubmit={createShow}>
          <p>Set the offer once. Fans can buy in during the pre-show and while the VIP show is active.</p>
          <label>VIP show title<input maxLength={160} name="vip-title" required /></label>
          <label>What you promise<textarea maxLength={1000} name="vip-description" required /></label>
          <label>VIP funding goal (EUR)<input min="0.01" name="vip-goal" step="0.01" type="number" required /></label>
          <label>Admission price (EUR)<input min="0.01" name="vip-buy-in" step="0.01" type="number" required /></label>
          <label>Pre-show countdown<select defaultValue="3" name="vip-preshow"><option value="1">1 minute</option><option value="2">2 minutes</option><option value="3">3 minutes</option><option value="4">4 minutes</option><option value="5">5 minutes</option></select></label>
          <label>VIP duration<select defaultValue="10" name="vip-duration"><option value="5">5 minutes</option><option value="10">10 minutes</option><option value="15">15 minutes</option></select></label>
          <button disabled={pending}>{pending ? "Starting…" : "Start VIP pre-show"}</button>
        </form>
      ) : (
        <div>
          <p className="eyebrow">{show.status.replace("_", " ").toUpperCase()}</p>
          <h3>{show.title}</h3>
          <p>{show.description}</p>
          <p><strong>{money(show.confirmed_amount_minor, show.currency)}</strong> of {money(show.goal_amount_minor, show.currency)} · {progress}%</p>
          <progress aria-label="VIP goal progress" max={show.goal_amount_minor} value={show.confirmed_amount_minor} />
          <p>Buy-in: {money(show.buy_in_amount_minor, show.currency)}</p>
          {show.status === "preshow" && <p>Pre-show ends in {remaining(show.preshow_ends_at, now)}</p>}
          {show.status === "active" && <p>VIP show ends in {remaining(show.ends_at, now)}</p>}
          {(show.status === "preshow" || show.status === "awaiting_creator") && <div className="button-row"><button disabled={pending || show.confirmed_amount_minor < 1} onClick={() => void control("start")} type="button">Start VIP now</button><button className="secondary-button" disabled={pending} onClick={() => void control("cancel")} type="button">Cancel and refund</button></div>}
        </div>
      )}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
