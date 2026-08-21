"use client";
import { FormEvent, useEffect, useState } from "react";
import { api } from "../lib/api";
type Conversation = { id: string; unread_count: number };
type Message = { id: string; body: string | null };
export function Inbox() {
  const [items, setItems] = useState<Conversation[]>([]); const [active, setActive] = useState<string>(); const [messages, setMessages] = useState<Message[]>([]); const [body, setBody] = useState("");
  const refresh = async () => setItems(await api<Conversation[]>("/messages/conversations"));
  useEffect(() => { void refresh(); const timer = setInterval(() => void refresh(), 15000); return () => clearInterval(timer); }, []);
  useEffect(() => { if (!active) return; const load = async () => { setMessages(await api<Message[]>(`/messages/conversations/${active}`)); await api<void>(`/messages/conversations/${active}/read`, { method: "POST" }); }; void load(); const timer = setInterval(() => void load(), 8000); return () => clearInterval(timer); }, [active]);
  const send = async (event: FormEvent) => { event.preventDefault(); if (!active || !body.trim()) return; await api(`/messages/conversations/${active}`, { method: "POST", body: JSON.stringify({ body }) }); setBody(""); setMessages(await api<Message[]>(`/messages/conversations/${active}`)); void refresh(); };
  return <div><h2>Inbox</h2>{items.map((item) => <button onClick={() => setActive(item.id)} key={item.id}>Conversation {item.unread_count ? `(${item.unread_count})` : ""}</button>)}{active ? <><div>{messages.map((item) => <p key={item.id}>{item.body ?? "Message removed"}</p>)}</div><form onSubmit={send}><label>Message<textarea value={body} onChange={(event) => setBody(event.target.value)} maxLength={4000} /></label><button>Send</button></form></> : <p>Select a conversation.</p>}</div>;
}
