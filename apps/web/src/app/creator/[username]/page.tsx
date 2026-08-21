"use client";

import { useEffect, useState } from "react";

import { api, ApiError } from "../../../lib/api";
import { SubscriptionOptions } from "../../../components/subscription-options";
import { Feed } from "../../../components/feed";
import { CreatorMessageComposer } from "../../../components/creator-message-composer";
import { PrivateSessionRequest } from "../../../components/private-session-request";

type Creator = {
  id: string;
  display_name: string;
  username: string;
  bio: string | null;
  location: string | null;
  verified: boolean;
};

type Content = {
  id: string;
  content_type: string;
  title: string;
  description: string | null;
  locked: boolean;
  access_policy: string;
  price_amount_minor: number | null;
  price_currency: string | null;
  previews: { derivative_id: string; delivery_path: string }[];
};

const apiBase = process.env.NEXT_PUBLIC_FANBACKSTAGE_API_URL ?? "http://localhost:8000";

export default function CreatorPage({ params }: { params: Promise<{ username: string }> }) {
  const [creator, setCreator] = useState<Creator | null>(null);
  const [content, setContent] = useState<Content[]>([]);
  const [error, setError] = useState("");
  const [purchasing, setPurchasing] = useState<string | null>(null);

  useEffect(() => {
    params
      .then(async ({ username }) => {
        const [profile, publishedContent] = await Promise.all([
          api<Creator>(`/creators/${username}`),
          api<Content[]>(`/content/public/by-creator/${username}`),
        ]);
        setCreator(profile);
        setContent(publishedContent);
      })
      .catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Creator not found"));
  }, [params]);

  if (error) {
    return <section className="card"><h1>Creator not found</h1><p className="error">{error}</p></section>;
  }
  if (!creator) {
    return <section className="card"><p>Loading creator profile…</p></section>;
  }
  async function purchase(contentId: string, creatorUsername: string) {
    setPurchasing(contentId);
    setError("");
    try {
      const started = await api<{ payment_attempt_id: string }>(`/purchases/content/${contentId}`, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
      });
      await api(`/payments/development/${started.payment_attempt_id}/complete`, { method: "POST" });
      const publishedContent = await api<Content[]>(`/content/public/by-creator/${creatorUsername}`);
      setContent(publishedContent);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "Purchase could not be completed");
    } finally {
      setPurchasing(null);
    }
  }
  return (
    <section className="card">
      <p className="eyebrow">{creator.verified ? "VERIFIED CREATOR" : "CREATOR"}</p>
      <h1>{creator.display_name}</h1>
      <p>@{creator.username}</p>
      {creator.bio && <p>{creator.bio}</p>}
      {creator.location && <p>{creator.location}</p>}
      <SubscriptionOptions username={creator.username} creatorId={creator.id} />
      <CreatorMessageComposer creatorId={creator.id} />
      <PrivateSessionRequest creatorId={creator.id} />
      <h2>Posts</h2>
      <Feed creatorId={creator.id} />
      <h2>Published content</h2>
      {content.length === 0 && <p>No content published yet.</p>}
      <div>
        {content.map((item) => (
          <article key={item.id} className="card">
            {item.previews.map((preview) => (
              <img
                alt={`${item.title} preview`}
                key={preview.derivative_id}
                src={`${apiBase}/api/v1${preview.delivery_path}`}
              />
            ))}
            <p className="eyebrow">{item.content_type}{item.locked ? " · LOCKED" : ""}</p>
            <h3>{item.title}</h3>
            {item.description && <p>{item.description}</p>}
            {item.locked && item.access_policy === "ppv" && item.price_amount_minor !== null && (
              <button disabled={purchasing === item.id} onClick={() => purchase(item.id, creator.username)}>
                {purchasing === item.id ? "Completing purchase…" : `Unlock for ${item.price_amount_minor} ${item.price_currency}`}
              </button>
            )}
          </article>
        ))}
      </div>
    </section>
  );
}
