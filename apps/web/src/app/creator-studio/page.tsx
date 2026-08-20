"use client";

import { FormEvent, useEffect, useState } from "react";

import { api, ApiError } from "../../lib/api";

type Asset = { id: string; status: string };
type Upload = Asset & { upload_url?: string };

export default function CreatorStudioPage() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { api<Asset[]>("/media/mine").then(setAssets).catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Unable to load media")); }, []);
  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const file = new FormData(event.currentTarget).get("file"); if (!(file instanceof File)) return;
    try { const created = await api<Upload>("/media/uploads", { method: "POST", body: JSON.stringify({ filename: file.name, mime_type: file.type }) }); if (!created.upload_url) throw new Error(); await fetch(created.upload_url, { method: "PUT", headers: { "Content-Type": file.type }, body: file }); const finalized = await api<Upload>(`/media/${created.id}/finalize`, { method: "POST" }); setAssets((items) => [...items, finalized]); setMessage("Upload queued for processing."); } catch (e) { setError(e instanceof ApiError ? e.message : "Upload failed"); }
  }
  return <section className="card"><p className="eyebrow">CREATOR STUDIO</p><h1>Media library</h1><form onSubmit={upload}><label>Upload image or video<input name="file" type="file" accept="image/jpeg,image/png,image/webp,video/mp4,video/webm" required /></label><button>Upload media</button></form><ul>{assets.map((asset) => <li key={asset.id}>{asset.id}: {asset.status}</li>)}</ul>{message && <p>{message}</p>}{error && <p className="error">{error}</p>}</section>;
}
