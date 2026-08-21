"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { Room } from "livekit-client";

import { api, ApiError } from "../lib/api";

type LiveRoom = { id: string; title: string; status: string; access_mode: string };
type Token = { provider_url: string; token: string };
type LiveSettings = {
  private_sessions_enabled: boolean;
  one_to_one_price_minor: number;
  two_to_one_price_minor: number;
  currency: string;
  minimum_minutes: number;
  max_authorization_minor: number;
};

export function LiveStudio() {
  const [room, setRoom] = useState<LiveRoom | null>(null);
  const [settings, setSettings] = useState<LiveSettings | null>(null);
  const [message, setMessage] = useState("");
  const providerRoom = useRef<Room | null>(null);

  useEffect(() => {
    void api<LiveSettings>("/live/settings").then(setSettings).catch(() => undefined);
  }, []);

  async function savePrivatePricing(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const values = new FormData(event.currentTarget);
    try {
      const updated = await api<LiveSettings>("/live/settings", {
        method: "PATCH",
        body: JSON.stringify({
          private_sessions_enabled: values.get("private-sessions-enabled") === "on",
          one_to_one_price_minor: Number(values.get("one-to-one-price")),
          two_to_one_price_minor: Number(values.get("two-to-one-price")),
          currency: values.get("private-currency"),
          minimum_minutes: Number(values.get("private-minimum-minutes")),
          max_authorization_minor: Number(values.get("private-max-authorization")),
        }),
      });
      setSettings(updated);
      setMessage("Private-session pricing saved. It applies to future requests only.");
    } catch (caught) {
      setMessage(caught instanceof ApiError ? caught.message : "Unable to save private-session pricing");
    }
  }

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const values = new FormData(event.currentTarget);
    try {
      const started = await api<LiveRoom>("/live/rooms", { method: "POST", body: JSON.stringify({ title: values.get("title"), access_mode: values.get("access_mode") }) });
      const authorization = await api<Token>(`/live/rooms/${started.id}/token`, { method: "POST" });
      const livekitRoom = new Room();
      await livekitRoom.connect(authorization.provider_url, authorization.token);
      await livekitRoom.localParticipant.enableCameraAndMicrophone();
      if (livekitRoom.localParticipant.trackPublications.size < 2) {
        throw new Error("Camera and microphone tracks were not published");
      }
      providerRoom.current = livekitRoom;
      setRoom(started); setMessage("Live room started with audio and video. Viewer tokens cannot publish.");
    } catch (caught) { setMessage(caught instanceof ApiError ? caught.message : "Unable to start live room"); }
  }

  async function end() {
    if (!room) return;
    try { providerRoom.current?.disconnect(); providerRoom.current = null; setRoom(await api<LiveRoom>(`/live/rooms/${room.id}/end`, { method: "POST" })); setMessage("Live room ended. You can now accept queued private requests."); }
    catch (caught) { setMessage(caught instanceof ApiError ? caught.message : "Unable to end live room"); }
  }

  return <section className="card" aria-label="Live Studio"><p className="eyebrow">LIVE STUDIO</p><h2>Go live</h2>{!room || room.status === "ended" ? <form onSubmit={start}><label>Live title<input name="title" maxLength={160} required /></label><label>Audience<select name="access_mode" defaultValue="public"><option value="public">Public</option><option value="followers">Followers</option><option value="subscribers">Subscribers</option></select></label><button>Start live</button></form> : <><p>{room.title} · {room.status}</p><button onClick={() => void end()}>End public live</button><p>Queued private sessions cannot be accepted until this public room has ended.</p></>}{settings && <form aria-label="Private session pricing" onSubmit={savePrivatePricing}><h2>Private session pricing</h2><label><input name="private-sessions-enabled" type="checkbox" defaultChecked={settings.private_sessions_enabled} /> Enable private sessions</label><label>1:1 per-minute price (minor units)<input name="one-to-one-price" type="number" min="1" defaultValue={settings.one_to_one_price_minor} required /></label><label>2-to-1 per-minute price (minor units)<input name="two-to-one-price" type="number" min="1" defaultValue={settings.two_to_one_price_minor} required /></label><label>Currency<input name="private-currency" defaultValue={settings.currency} minLength={3} maxLength={3} required /></label><label>Minimum minutes<input name="private-minimum-minutes" type="number" min="1" defaultValue={settings.minimum_minutes} required /></label><label>Maximum authorization (minor units)<input name="private-max-authorization" type="number" min="1" defaultValue={settings.max_authorization_minor} required /></label><button>Save private-session pricing</button></form>}{message && <p>{message}</p>}</section>;
}
