"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { Room, RoomEvent, Track } from "livekit-client";

import { api, ApiError } from "../lib/api";

type RoomSummary = { id: string; title: string; status: string; access_mode: string; viewer_count: number };
type Chat = { id: string; body: string; sender_user_id: string | null };
type Token = { room_id: string; provider_url: string; token: string };

export function LiveNow() {
  const [rooms, setRooms] = useState<RoomSummary[]>([]);
  const [active, setActive] = useState<RoomSummary | null>(null);
  const [chat, setChat] = useState<Chat[]>([]);
  const [error, setError] = useState("");
  const roomRef = useRef<Room | null>(null);
  const videoRef = useRef<HTMLDivElement | null>(null);

  async function refresh() {
    try { setRooms(await api<RoomSummary[]>("/live/rooms")); }
    catch (caught) { setError(caught instanceof ApiError ? caught.message : "Unable to load live rooms"); }
  }

  function disconnect() {
    roomRef.current?.disconnect(); roomRef.current = null; videoRef.current?.replaceChildren(); setActive(null);
  }

  async function join(room: RoomSummary) {
    try {
      disconnect();
      const authorization = await api<Token>(`/live/rooms/${room.id}/token`, { method: "POST" });
      const livekitRoom = new Room();
      livekitRoom.on(RoomEvent.TrackSubscribed, (track) => {
        if (track.kind === Track.Kind.Video && videoRef.current) videoRef.current.append(track.attach());
      });
      livekitRoom.on(RoomEvent.TrackUnsubscribed, (track) => track.detach().forEach((element) => element.remove()));
      await livekitRoom.connect(authorization.provider_url, authorization.token);
      roomRef.current = livekitRoom; setActive(room);
      setChat(await api<Chat[]>(`/live/rooms/${room.id}/chat`));
    } catch (caught) {
      disconnect(); setError(caught instanceof ApiError ? caught.message : "Unable to connect to this live room");
    }
  }

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!active) return;
    const form = new FormData(event.currentTarget);
    try {
      await api(`/live/rooms/${active.id}/chat`, { method: "POST", body: JSON.stringify({ body: form.get("body") }) });
      event.currentTarget.reset(); setChat(await api<Chat[]>(`/live/rooms/${active.id}/chat`));
    } catch (caught) { setError(caught instanceof ApiError ? caught.message : "Unable to send chat"); }
  }

  useEffect(() => {
    void refresh(); const timer = setInterval(() => void refresh(), 15_000);
    return () => { clearInterval(timer); disconnect(); };
  }, []);

  return <section className="card" aria-label="Live now">
    <p className="eyebrow">LIVE NOW</p><h1>Live creators</h1>{error && <p className="error">{error}</p>}
    {rooms.map((room) => <article key={room.id}><h2>{room.title}</h2><p>{room.access_mode} · {room.viewer_count} watching</p><button onClick={() => void join(room)}>Watch live</button></article>)}
    {!rooms.length && <p>No creators are live right now.</p>}
    {active && <section aria-label="Live room"><h2>Watching: {active.title}</h2><div ref={videoRef} aria-label="Live video" /><p>Video uses a short-lived server-authorized LiveKit token. Chat history is durable; REST polling remains available if realtime delivery degrades.</p><button onClick={disconnect}>Leave live</button>{chat.map((message) => <p key={message.id}>{message.body}</p>)}<form onSubmit={send}><label>Live chat<input name="body" maxLength={1000} required /></label><button>Send</button></form></section>}
  </section>;
}
