"use client";

import { FormEvent, useEffect, useState } from "react";

import { api, ApiError } from "../lib/api";

type Room = { id: string; public_id: string; creator_id: string; status: string; access_mode: string; title: string; description: string | null; viewer_count: number };
type Chat = { id: string; body: string; sender_user_id: string | null };

export function LiveNow() {
  const [rooms, setRooms] = useState<Room[]>([]); const [active, setActive] = useState<Room | null>(null); const [chat, setChat] = useState<Chat[]>([]); const [error, setError] = useState("");
  async function refresh() { try { setRooms(await api<Room[]>("/live/rooms")); } catch (e) { setError(e instanceof ApiError ? e.message : "Unable to load live rooms"); } }
  async function join(room: Room) { try { await api(`/live/rooms/${room.id}/join`, { method: "POST" }); setActive(room); setChat(await api<Chat[]>(`/live/rooms/${room.id}/chat`)); } catch (e) { setError(e instanceof ApiError ? e.message : "Unable to join live room"); } }
  async function send(event: FormEvent<HTMLFormElement>) { event.preventDefault(); if (!active) return; const form = new FormData(event.currentTarget); try { await api(`/live/rooms/${active.id}/chat`, { method: "POST", body: JSON.stringify({ body: form.get("body") }) }); event.currentTarget.reset(); setChat(await api<Chat[]>(`/live/rooms/${active.id}/chat`)); } catch (e) { setError(e instanceof ApiError ? e.message : "Unable to send chat"); } }
  useEffect(() => { void refresh(); const timer = setInterval(() => void refresh(), 15000); return () => clearInterval(timer); }, []);
  return <section className="card" aria-label="Live now"><p className="eyebrow">LIVE NOW</p><h1>Live creators</h1>{error && <p className="error">{error}</p>}{rooms.map(room => <article key={room.id}><h2>{room.title}</h2><p>{room.access_mode} · {room.viewer_count} watching</p><button onClick={() => void join(room)}>Watch live</button></article>)}{!rooms.length && <p>No creators are live right now.</p>}{active && <section><h2>Watching: {active.title}</h2><p>Live video connects through the server-authorized room token. Chat history is durable; realtime delivery is an enhancement.</p>{chat.map(message => <p key={message.id}>{message.body}</p>)}<form onSubmit={send}><label>Live chat<input name="body" maxLength={1000} required /></label><button>Send</button></form></section>}</section>;
}
