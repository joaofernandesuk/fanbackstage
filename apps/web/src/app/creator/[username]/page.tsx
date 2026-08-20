"use client";

import { useEffect, useState } from "react";

import { api, ApiError } from "../../../lib/api";

type Creator = { display_name: string; username: string; bio: string | null; location: string | null; verified: boolean };

export default function CreatorPage({ params }: { params: Promise<{ username: string }> }) {
  const [creator, setCreator] = useState<Creator | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { params.then(({ username }) => api<Creator>(`/creators/${username}`).then(setCreator).catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Creator not found"))); }, [params]);
  if (error) return <section className="card"><h1>Creator not found</h1><p className="error">{error}</p></section>;
  if (!creator) return <section className="card"><p>Loading creator profile…</p></section>;
  return <section className="card"><p className="eyebrow">{creator.verified ? "VERIFIED CREATOR" : "CREATOR"}</p><h1>{creator.display_name}</h1><p>@{creator.username}</p>{creator.bio && <p>{creator.bio}</p>}{creator.location && <p>{creator.location}</p>}</section>;
}
