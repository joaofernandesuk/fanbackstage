"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "../lib/api";

type Post = { id: string; creator_id: string; creator_username: string; creator_name: string; body: string | null; post_type: string; access_policy: string; locked: boolean; published_at: string | null; reaction_count: number; comment_count: number; viewer_reaction: string | null; reactions_enabled: boolean; comments_enabled: boolean; content_reference: { id: string; title: string; locked: boolean; access_policy: string; price_amount_minor: number | null; price_currency: string | null } | null };
type Page = { items: Post[]; next_cursor: string | null };

export function Feed({ creatorId }: { creatorId?: string }) {
  const [tab, setTab] = useState("following");
  const [items, setItems] = useState<Post[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState("");
  const path = creatorId ? `/feed/creator/${creatorId}` : `/feed/${tab}`;
  async function load(next?: string | null) {
    try {
      const page = await api<Page>(`${path}${next ? `?cursor=${encodeURIComponent(next)}` : ""}`);
      setItems((old) => next ? [...old, ...page.items] : page.items); setCursor(page.next_cursor); setError("");
    } catch (e) { setError(e instanceof ApiError ? e.message : "Unable to load posts"); }
  }
  useEffect(() => { setCursor(null); void load(); }, [path]);
  async function react(post: Post) {
    try { await api(`/feed/posts/${post.id}/reaction`, { method: post.viewer_reaction ? "DELETE" : "PUT", body: post.viewer_reaction ? undefined : JSON.stringify({ reaction_type: "like" }) }); await load(); } catch (e) { setError(e instanceof ApiError ? e.message : "Unable to react"); }
  }
  return <section aria-label="Creator feed">
    {!creatorId && <div className="tabs"><button aria-pressed={tab === "following"} onClick={() => setTab("following")}>Following</button><button aria-pressed={tab === "discover"} onClick={() => setTab("discover")}>Discover</button></div>}
    {error && <p className="error">{error}</p>}
    {items.map((post) => <article className="card" key={post.id}>
      <p className="eyebrow">@{post.creator_username} · {post.post_type}{post.locked ? " · LOCKED" : ""}</p><h3>{post.creator_name}</h3>
      {post.locked ? <p>This post is available to {post.access_policy === "followers" ? "followers" : "subscribers"}.</p> : <p>{post.body}</p>}
      {post.content_reference && <p><a href={`/creator/${post.creator_username}`}>{post.content_reference.title}{post.content_reference.locked ? " · locked" : ""}</a></p>}
      <button disabled={!post.reactions_enabled || post.locked} onClick={() => react(post)}>{post.viewer_reaction ? "Unlike" : "Like"} ({post.reaction_count})</button><span> {post.comment_count} comments</span>
    </article>)}
    {cursor && <button onClick={() => void load(cursor)}>Load more</button>}
  </section>;
}
