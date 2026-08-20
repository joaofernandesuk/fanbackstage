"use client";

import { FormEvent, useEffect, useState } from "react";

import { api, ApiError } from "../../lib/api";

type Asset = { id: string; status: string; media_type?: string };
type Upload = Asset & { upload_url?: string };
type Content = { id: string; title: string; content_type: string; status: string };

const policies = ["free", "followers", "subscription", "ppv", "private"];

export default function CreatorStudioPage() {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [content, setContent] = useState<Content[]>([]);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const readyImages = assets.filter((asset) => asset.status === "ready" && asset.media_type === "image");
  const readyVideos = assets.filter((asset) => asset.status === "ready" && asset.media_type === "video");

  async function refresh() {
    const [media, managedContent] = await Promise.all([
      api<Asset[]>("/media/mine"),
      api<Content[]>("/content/mine"),
    ]);
    setAssets(media);
    setContent(managedContent);
  }

  useEffect(() => { refresh().catch((e: unknown) => setError(e instanceof ApiError ? e.message : "Unable to load creator media")); }, []);

  async function upload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const file = new FormData(event.currentTarget).get("file");
    if (!(file instanceof File)) return;
    try {
      const created = await api<Upload>("/media/uploads", { method: "POST", body: JSON.stringify({ filename: file.name, mime_type: file.type }) });
      if (!created.upload_url) throw new Error("The upload authorization is missing");
      await fetch(created.upload_url, { method: "PUT", headers: { "Content-Type": file.type }, body: file });
      await api<Upload>(`/media/${created.id}/finalize`, { method: "POST" });
      setMessage("Upload queued for processing.");
      await refresh();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Upload failed"); }
  }

  async function createGallery(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const selected = form.getAll("image").map(String);
    if (!selected.length) { setError("Choose at least one ready image."); return; }
    try {
      const gallery = await api<Content>("/content/galleries", { method: "POST", body: JSON.stringify({ title: form.get("gallery-title"), access_policy: form.get("gallery-policy") }) });
      for (const media_asset_id of selected) await api(`/content/galleries/${gallery.id}/items`, { method: "POST", body: JSON.stringify({ media_asset_id }) });
      await api(`/content/galleries/${gallery.id}/preview`, { method: "PATCH", body: JSON.stringify({ preview_count: 1, preview_asset_ids: [selected[0]] }) });
      await api(`/content/${gallery.id}/publish`, { method: "POST" });
      setMessage("Gallery published with its selected secure preview.");
      await refresh();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Gallery could not be published"); }
  }

  async function createVideo(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const video = await api<Content>("/content/videos", { method: "POST", body: JSON.stringify({ title: form.get("video-title"), access_policy: form.get("video-policy"), media_asset_id: form.get("video") }) });
      await api(`/content/${video.id}/publish`, { method: "POST" });
      setMessage("Video published. Its poster and preview remain separate derivatives.");
      await refresh();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Video could not be published"); }
  }

  return <section className="card"><p className="eyebrow">CREATOR STUDIO</p><h1>Media library</h1><form onSubmit={upload}><label>Upload image or video<input name="file" type="file" accept="image/jpeg,image/png,image/webp,video/mp4,video/webm" required /></label><button>Upload media</button></form><h2>Gallery editor</h2><form onSubmit={createGallery}><label>Gallery title<input name="gallery-title" required /></label><label>Access policy<select name="gallery-policy">{policies.map((policy) => <option key={policy}>{policy}</option>)}</select></label><fieldset><legend>Ready images</legend>{readyImages.map((asset) => <label key={asset.id}><input name="image" type="checkbox" value={asset.id} />{asset.id}</label>)}</fieldset><button>Create and publish gallery</button></form><h2>Video editor</h2><form onSubmit={createVideo}><label>Video title<input name="video-title" required /></label><label>Access policy<select name="video-policy">{policies.map((policy) => <option key={policy}>{policy}</option>)}</select></label><label>Ready video<select name="video" required><option value="">Select media</option>{readyVideos.map((asset) => <option key={asset.id} value={asset.id}>{asset.id}</option>)}</select></label><button>Create and publish video</button></form><h2>Library status</h2><ul>{assets.map((asset) => <li key={asset.id}>{asset.id}: {asset.status}</li>)}</ul><h2>Content</h2><ul>{content.map((item) => <li key={item.id}>{item.content_type}: {item.title} ({item.status})</li>)}</ul>{message && <p>{message}</p>}{error && <p className="error">{error}</p>}</section>;
}
