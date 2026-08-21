"use client";

import { FormEvent, useRef, useState } from "react";
import { Room } from "livekit-client";

import { api, ApiError } from "../lib/api";

type LiveRoom = { id: string; title: string; status: string; access_mode: string };
type Token = { provider_url: string; token: string };

export function LiveStudio() {
  const [room, setRoom] = useState<LiveRoom | null>(null);
  const [message, setMessage] = useState("");
  const providerRoom = useRef<Room | null>(null);

  async function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const values = new FormData(event.currentTarget);
    try {
      const started = await api<LiveRoom>("/live/rooms", { method: "POST", body: JSON.stringify({ title: values.get("title"), access_mode: values.get("access_mode") }) });
      const authorization = await api<Token>(`/live/rooms/${started.id}/token`, { method: "POST" });
      const livekitRoom = new Room();
      await livekitRoom.connect(authorization.provider_url, authorization.token);
      await livekitRoom.localParticipant.enableCameraAndMicrophone();
      providerRoom.current = livekitRoom;
      setRoom(started); setMessage("Live room started. Viewer tokens cannot publish.");
    } catch (caught) { setMessage(caught instanceof ApiError ? caught.message : "Unable to start live room"); }
  }

  async function end() {
    if (!room) return;
    try { providerRoom.current?.disconnect(); providerRoom.current = null; setRoom(await api<LiveRoom>(`/live/rooms/${room.id}/end`, { method: "POST" })); setMessage("Live room ended. You can now accept queued private requests."); }
    catch (caught) { setMessage(caught instanceof ApiError ? caught.message : "Unable to end live room"); }
  }

  return <section className="card" aria-label="Live Studio"><p className="eyebrow">LIVE STUDIO</p><h2>Go live</h2>{!room || room.status === "ended" ? <form onSubmit={start}><label>Live title<input name="title" maxLength={160} required /></label><label>Audience<select name="access_mode" defaultValue="public"><option value="public">Public</option><option value="followers">Followers</option><option value="subscribers">Subscribers</option></select></label><button>Start live</button></form> : <><p>{room.title} · {room.status}</p><button onClick={() => void end()}>End public live</button><p>Queued private sessions cannot be accepted until this public room has ended.</p></>}{message && <p>{message}</p>}</section>;
}
