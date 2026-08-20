"use client";

import { useEffect, useState } from "react";

import { api, ApiError } from "../../../lib/api";

type Creator = {
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
  previews: { derivative_id: string; delivery_path: string }[];
};

const apiBase = process.env.NEXT_PUBLIC_FANBACKSTAGE_API_URL ?? "http://localhost:8000";

export default function CreatorPage({ params }: { params: Promise<{ username: string }> }) {
  const [creator, setCreator] = useState<Creator | null>(null);
  const [content, setContent] = useState<Content[]>([]);
  const [error, setError] = useState("");

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
  return (
    <section className="card">
      <p className="eyebrow">{creator.verified ? "VERIFIED CREATOR" : "CREATOR"}</p>
      <h1>{creator.display_name}</h1>
      <p>@{creator.username}</p>
      {creator.bio && <p>{creator.bio}</p>}
      {creator.location && <p>{creator.location}</p>}
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
          </article>
        ))}
      </div>
    </section>
  );
}
