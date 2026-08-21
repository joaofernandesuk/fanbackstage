"use client";
import { FormEvent, useEffect, useState } from "react";
import { api } from "../lib/api";
import { MessageAttachments } from "./message-attachments";
type Conversation = { id: string; other_user_id: string; unread_count: number; archived: boolean; muted: boolean };
type Message = { id: string; body: string | null };
export function Inbox() {
  const [items, setItems] = useState<Conversation[]>([]); const [active, setActive] = useState<string>(); const [messages, setMessages] = useState<Message[]>([]); const [body, setBody] = useState("");
  const refresh = async () => setItems(await api<Conversation[]>("/messages/conversations"));
  useEffect(() => { void refresh(); const timer = setInterval(() => void refresh(), 15000); return () => clearInterval(timer); }, []);
  useEffect(() => { if (!active) return; const load = async () => { setMessages(await api<Message[]>(`/messages/conversations/${active}`)); await api<void>(`/messages/conversations/${active}/read`, { method: "POST" }); }; void load(); const timer = setInterval(() => void load(), 8000); return () => clearInterval(timer); }, [active]);
  const send = async (event: FormEvent) => { event.preventDefault(); if (!active || !body.trim()) return; await api(`/messages/conversations/${active}`, { method: "POST", body: JSON.stringify({ body }) }); setBody(""); setMessages(await api<Message[]>(`/messages/conversations/${active}`)); void refresh(); };
  const current = items.find((item) => item.id === active);
  async function control(path: string, method = "POST") { await api(path, { method }); await refresh(); }
  async function report(messageId: string) { const reason = window.prompt("Why are you reporting this message?"); if (reason) await api(`/messages/messages/${messageId}/report`, { method: "POST", body: JSON.stringify({ reason }) }); }
  return <div><h2>Inbox</h2>{items.map((item) => <button onClick={() => setActive(item.id)} key={item.id}>Conversation {item.unread_count ? `(${item.unread_count})` : ""}{item.archived ? " · archived" : ""}{item.muted ? " · muted" : ""}</button>)}{active ? <><div>{messages.map((item) => <p key={item.id}>{item.body ?? "Message removed"} <button onClick={() => void report(item.id)}>Report</button></p>)}</div><MessageAttachments conversationId={active} />{current && <p><button onClick={() => void control(`/messages/conversations/${active}/archive`, current.archived ? "DELETE" : "POST")}>{current.archived ? "Unarchive" : "Archive"}</button><button onClick={() => void control(`/messages/conversations/${active}/mute`, current.muted ? "DELETE" : "POST")}>{current.muted ? "Unmute" : "Mute"}</button><button onClick={() => void control(`/messages/block/${current.other_user_id}`)}>Block user</button></p>}<form onSubmit={send}><label>Message<textarea value={body} onChange={(event) => setBody(event.target.value)} maxLength={4000} /></label><button>Send</button></form></> : <p>Select a conversation.</p>}</div>;
}
