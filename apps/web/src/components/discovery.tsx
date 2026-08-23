"use client";

import { FormEvent, useEffect, useState } from "react";

import { ApiError, api } from "../lib/api";

type Result = { entity_type: string; id: string; title: string; subtitle?: string | null; description?: string | null; locked: boolean; access_policy?: string | null; price_amount_minor?: number | null; currency?: string | null; availability?: string | null; live: boolean; reason?: string | null };
type Page = { items: Result[]; next_cursor: string | null; ranking_version: number };

export function Discovery({ initialQuery = "" }: { initialQuery?: string }) {
  const [query, setQuery] = useState(initialQuery), [page, setPage] = useState<Page | null>(null), [error, setError] = useState("");
  async function load(cursor?: string) { try { const path = query.trim() ? `/discovery/search?q=${encodeURIComponent(query)}${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}` : `/discovery/discover${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`; const next = await api<Page>(path); setPage(old => cursor && old ? { ...next, items: [...old.items, ...next.items] } : next); setError(""); } catch (e: unknown) { setError(e instanceof ApiError ? e.message : "Unable to load discovery"); } }
  useEffect(() => { void load(); }, []);
  function submit(event: FormEvent) { event.preventDefault(); void load(); }
  return <section><form onSubmit={submit} className="discovery-search"><label>Search FanBackstage<input aria-label="Search FanBackstage" value={query} onChange={event => setQuery(event.target.value)} minLength={2} placeholder="Creators, content, marketplace, live" /></label><button>Search</button></form>{error && <p className="error">{error}</p>}<p className="eyebrow">{query ? "SEARCH RESULTS" : "DISCOVER"}</p><div className="discovery-grid">{page?.items.map(item => <article className="card" key={`${item.entity_type}-${item.id}`}><small>{item.live ? "LIVE NOW" : item.entity_type.replaceAll("_", " ")}</small><h2>{item.title}</h2>{item.subtitle && <p>{item.subtitle}</p>}{item.description && <p>{item.locked ? "Locked content · safe preview only" : item.description}</p>}{item.locked && <strong>Locked · {item.access_policy}</strong>}{item.price_amount_minor != null && <p>{(item.price_amount_minor / 100).toFixed(2)} {item.currency} · {item.availability}</p>}<small>{item.reason}</small></article>)}</div>{page && !page.items.length && <p>No public results found.</p>}{page?.next_cursor && <button onClick={() => { if (page.next_cursor) void load(page.next_cursor); }}>Load more</button>}</section>;
}
