"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api, ApiError, type CurrentUser } from "../lib/api";
import {
  creatorUsernameFor,
  discoverySearchPath,
  type DiscoveryPage,
  type DiscoveryResult,
} from "../lib/public-api";
import { CreatorAvatar, EmptyState, Skeleton, useLoginGate } from "./consumer-ui";
import { MessageAttachments } from "./message-attachments";
import styles from "./social-surface.module.css";

type Conversation = {
  id: string;
  creator_id: string;
  viewer_user_id: string;
  other_user_id: string;
  last_message_at: string | null;
  unread_count: number;
  archived: boolean;
  muted: boolean;
};
type Message = {
  id: string;
  conversation_id: string;
  sender_user_id: string;
  body: string | null;
  status: string;
  created_at: string;
};
type CreatorSelf = { id: string };

function shortTime(value: string | null) {
  if (!value) return "New";
  const date = new Date(value);
  const elapsed = Date.now() - date.getTime();
  if (elapsed < 86_400_000) return new Intl.DateTimeFormat("en", { hour: "2-digit", minute: "2-digit" }).format(date);
  return new Intl.DateTimeFormat("en", { month: "short", day: "numeric" }).format(date);
}

function fanName(id: string) {
  return `Fan ${id.slice(0, 4).toUpperCase()}`;
}

export function Inbox() {
  const { authenticated, loading: authLoading } = useLoginGate();
  const [user, setUser] = useState<CurrentUser | null>(null);
  const [ownCreatorId, setOwnCreatorId] = useState<string | null>(null);
  const [creators, setCreators] = useState<DiscoveryResult[]>([]);
  const [items, setItems] = useState<Conversation[]>([]);
  const [active, setActive] = useState<string>();
  const [messages, setMessages] = useState<Message[]>([]);
  const [previews, setPreviews] = useState<Record<string, string>>({});
  const [body, setBody] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const streamRef = useRef<HTMLDivElement | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [conversations, identity, directory] = await Promise.all([
        api<Conversation[]>("/messages/conversations?limit=50"),
        api<CurrentUser>("/me"),
        api<DiscoveryPage>(discoverySearchPath({ types: ["creator"], limit: 50 })),
      ]);
      setItems(conversations);
      setUser(identity);
      setCreators(directory.items.filter((item) => item.entity_type === "creator"));
      if (identity.roles.includes("creator")) {
        const profile = await api<CreatorSelf>("/creators/me").catch(() => null);
        setOwnCreatorId(profile?.id || null);
      }
      const nextPreviews = await Promise.all(conversations.map(async (conversation) => {
        const history = await api<Message[]>(`/messages/conversations/${conversation.id}?limit=50`).catch(() => []);
        const latest = history.at(-1);
        return [conversation.id, latest?.body || (latest ? "Media message" : "Start the conversation")] as const;
      }));
      setPreviews(Object.fromEntries(nextPreviews));
      setError("");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to load conversations");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!authenticated) {
      if (!authLoading) setLoading(false);
      return;
    }
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15_000);
    return () => window.clearInterval(timer);
  }, [authenticated, authLoading, refresh]);

  useEffect(() => {
    if (!active) return;
    let mounted = true;
    const load = async () => {
      try {
        const history = await api<Message[]>(`/messages/conversations/${active}`);
        if (!mounted) return;
        setMessages(history);
        await api<void>(`/messages/conversations/${active}/read`, { method: "POST" });
        setItems((current) => current.map((item) => item.id === active ? { ...item, unread_count: 0 } : item));
        requestAnimationFrame(() => streamRef.current?.scrollTo({ top: streamRef.current.scrollHeight }));
      } catch (caught) {
        if (mounted) setError(caught instanceof ApiError ? caught.message : "Unable to load this conversation");
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), 8_000);
    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, [active]);

  const identity = useCallback((conversation: Conversation) => {
    const creator = creators.find((item) => item.id === conversation.creator_id || item.creator_id === conversation.creator_id);
    const isCreatorSide = ownCreatorId === conversation.creator_id;
    return isCreatorSide
      ? { name: fanName(conversation.other_user_id), username: undefined }
      : { name: creator?.title || "FanBackstage creator", username: creator ? creatorUsernameFor(creator) : undefined };
  }, [creators, ownCreatorId]);

  const visibleItems = useMemo(() => items.filter((item) => {
    const value = identity(item);
    return `${value.name} ${value.username || ""}`.toLowerCase().includes(search.toLowerCase());
  }), [identity, items, search]);
  const current = items.find((item) => item.id === active);
  const currentIdentity = current ? identity(current) : null;

  async function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!active || !body.trim()) return;
    try {
      await api(`/messages/conversations/${active}`, {
        method: "POST",
        body: JSON.stringify({ body }),
      });
      setBody("");
      const history = await api<Message[]>(`/messages/conversations/${active}`);
      setMessages(history);
      setPreviews((value) => ({ ...value, [active]: body }));
      void refresh();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to send your message");
    }
  }

  async function control(path: string, method = "POST") {
    try {
      await api(path, { method });
      await refresh();
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to update this conversation");
    }
  }

  async function report(messageId: string) {
    const reason = window.prompt("Tell us briefly why you are reporting this message.");
    if (!reason?.trim()) return;
    try {
      await api(`/messages/messages/${messageId}/report`, {
        method: "POST",
        body: JSON.stringify({ reason: reason.trim().slice(0, 80) }),
      });
      setError("Report received. Our safety team will review it.");
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : "Unable to report this message");
    }
  }

  if (loading || authLoading) {
    return <div className={styles.inboxShell}><Skeleton lines={5} /><Skeleton lines={4} /></div>;
  }

  if (!authenticated) {
    return (
      <EmptyState
        action={<><Link className={styles.primaryLink} href="/login?next=%2Fmessages">Log in</Link> <Link className={styles.secondaryLink} href="/creators">Discover creators</Link></>}
        body="Log in to see your conversations, unlock authorized media, and message creators."
        title="Your messages are private"
      />
    );
  }

  return (
    <div className={styles.inboxShell} data-conversation-open={Boolean(active)}>
      <aside aria-label="Conversations" className={styles.conversationPane}>
        <div className={styles.inboxTitle}><h1>Messages</h1><span>{items.reduce((total, item) => total + item.unread_count, 0)} unread</span></div>
        <label className={styles.inboxSearch}>
          <span className="sr-only">Search conversations</span>
          <input onChange={(event) => setSearch(event.target.value)} placeholder="Search messages" type="search" value={search} />
        </label>
        <div className={styles.conversationList}>
          {visibleItems.map((item) => {
            const person = identity(item);
            return (
              <button
                aria-label={`Conversation with ${person.name}`}
                aria-pressed={active === item.id}
                className={styles.conversationButton}
                key={item.id}
                onClick={() => setActive(item.id)}
                type="button"
              >
                <CreatorAvatar displayName={person.name} size={45} username={person.username} />
                <span className={styles.conversationText}>
                  <strong>{person.name}</strong>
                  <span>{previews[item.id] || "Conversation"}{item.archived ? " · Archived" : ""}{item.muted ? " · Muted" : ""}</span>
                </span>
                <span className={styles.conversationMeta}>
                  <time dateTime={item.last_message_at || undefined}>{shortTime(item.last_message_at)}</time>
                  {item.unread_count > 0 && <b>{item.unread_count > 99 ? "99+" : item.unread_count}</b>}
                </span>
              </button>
            );
          })}
          {!visibleItems.length && <EmptyState action={<Link className={styles.secondaryLink} href="/creators">Discover creators</Link>} body="Find a creator you like and start a respectful conversation from their profile." title="No conversations yet" />}
        </div>
      </aside>

      <section aria-label="Selected conversation" className={styles.messagePane}>
        {current && currentIdentity ? (
          <>
            <header className={styles.messageHeader}>
              <button aria-label="Back to conversations" className={styles.backButton} onClick={() => setActive(undefined)} type="button">←</button>
              {currentIdentity.username ? (
                <Link href={`/creator/${currentIdentity.username}`}><CreatorAvatar displayName={currentIdentity.name} size={42} username={currentIdentity.username} /></Link>
              ) : <CreatorAvatar displayName={currentIdentity.name} size={42} />}
              <div><strong>{currentIdentity.name}</strong><span>Private conversation</span></div>
              <div className={styles.messageControls}>
                <button onClick={() => void control(`/messages/conversations/${active}/archive`, current.archived ? "DELETE" : "POST")} type="button">{current.archived ? "Unarchive" : "Archive"}</button>
                <button onClick={() => void control(`/messages/conversations/${active}/mute`, current.muted ? "DELETE" : "POST")} type="button">{current.muted ? "Unmute" : "Mute"}</button>
                <button onClick={() => void control(`/messages/block/${current.other_user_id}`)} type="button">Block</button>
              </div>
            </header>
            <div className={styles.messageStream} ref={streamRef}>
              {messages.map((message) => (
                <article className={`${styles.messageBubble} ${message.sender_user_id === user?.id ? styles.messageBubbleOwn : ""}`} key={message.id}>
                  <p>{message.body ?? "Message removed"}</p>
                  <time dateTime={message.created_at}>{shortTime(message.created_at)} · {message.status}</time>
                  {message.sender_user_id !== user?.id && <button className={styles.reportMessage} onClick={() => void report(message.id)} type="button">Report</button>}
                </article>
              ))}
              <MessageAttachments conversationId={current.id} />
            </div>
            <form className={styles.messageComposer} onSubmit={send}>
              <label className="sr-only" htmlFor="message-body">Message</label>
              <textarea id="message-body" maxLength={4000} onChange={(event) => setBody(event.target.value)} placeholder="Write a message…" value={body} />
              <button disabled={!body.trim()} type="submit">Send</button>
            </form>
          </>
        ) : (
          <div className={styles.conversationEmpty}>
            <CreatorAvatar displayName="FanBackstage" size={64} />
            <h2>Your conversations</h2>
            <p>Choose a conversation to see your messages and authorized media.</p>
          </div>
        )}
      </section>
      {error && <p className={styles.inlineMessage} role="status">{error}</p>}
    </div>
  );
}
