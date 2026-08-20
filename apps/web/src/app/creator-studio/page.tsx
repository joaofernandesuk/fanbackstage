"use client";

import { FormEvent, useEffect, useState } from "react";

import { api, ApiError } from "../../lib/api";
import { CreatorEarnings } from "../../components/creator-earnings";
import { SubscriptionSettings } from "../../components/subscription-settings";

type Asset = { id: string; status: string; media_type?: string };
type Upload = Asset & { upload_url?: string };
type Content = { id: string; title: string; content_type: string; status: string; access_policy: string; price_amount_minor?: number | null; price_currency?: string | null };

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
      const uploadResponse = await fetch(created.upload_url, { method: "PUT", headers: { "Content-Type": file.type }, body: file });
      if (!uploadResponse.ok) throw new Error(`Storage upload failed (${uploadResponse.status})`);
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
      const policy = String(form.get("gallery-policy"));
      const price = String(form.get("gallery-price-minor") || "");
      const price_amount_minor = price ? Number(price) : undefined;
      if (policy === "ppv" && price_amount_minor === undefined) throw new Error("PPV content requires a price.");
      if (price && (price_amount_minor === undefined || !Number.isSafeInteger(price_amount_minor) || price_amount_minor <= 0)) throw new Error("Price must be a positive whole number of minor units.");
      const gallery = await api<Content>("/content/galleries", { method: "POST", body: JSON.stringify({ title: form.get("gallery-title"), access_policy: policy, ...(policy === "ppv" ? { price_amount_minor, price_currency: String(form.get("gallery-currency") || "EUR").toUpperCase() } : {}) }) });
      for (const media_asset_id of selected) await api(`/content/galleries/${gallery.id}/items`, { method: "POST", body: JSON.stringify({ media_asset_id }) });
      const cover = String(form.get("gallery-cover") || selected[0]);
      const previewCount = Number(form.get("gallery-preview-count") || 0);
      if (!selected.includes(cover)) { setError("The cover image must be one of the selected gallery images."); return; }
      await api(`/content/galleries/${gallery.id}/cover`, { method: "PATCH", body: JSON.stringify({ media_asset_id: cover }) });
      await api(`/content/galleries/${gallery.id}/preview`, { method: "PATCH", body: JSON.stringify({ preview_count: previewCount, preview_asset_ids: [] }) });
      await api(`/content/${gallery.id}/submit`, { method: "POST" });
      setMessage("Gallery submitted for review with its secure preview configuration.");
      await refresh();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Gallery could not be published"); }
  }

  async function createVideo(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const policy = String(form.get("video-policy"));
      const price = String(form.get("video-price-minor") || "");
      const price_amount_minor = price ? Number(price) : undefined;
      if (policy === "ppv" && price_amount_minor === undefined) throw new Error("PPV content requires a price.");
      if (price && (price_amount_minor === undefined || !Number.isSafeInteger(price_amount_minor) || price_amount_minor <= 0)) throw new Error("Price must be a positive whole number of minor units.");
      await api<Content>("/content/videos", { method: "POST", body: JSON.stringify({ title: form.get("video-title"), access_policy: policy, media_asset_id: form.get("video"), preview_start_seconds: Number(form.get("video-preview-start") || 0), preview_duration_seconds: Number(form.get("video-preview-duration") || 20), ...(policy === "ppv" ? { price_amount_minor, price_currency: String(form.get("video-currency") || "EUR").toUpperCase() } : {}) }) });
      setMessage("Video preview rendering is queued. Submit it for review once processing is complete.");
      await refresh();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Video could not be published"); }
  }

  async function submitForReview(contentId: string) {
    try {
      await api(`/content/${contentId}/submit`, { method: "POST" });
      setMessage("Content submitted for review.");
      await refresh();
    } catch (e) { setError(e instanceof ApiError ? e.message : "Content is not ready for review"); }
  }

  return <section className="card"><p className="eyebrow">CREATOR STUDIO</p><h1>Media library</h1><CreatorEarnings /><SubscriptionSettings /><form onSubmit={upload}><label>Upload image or video<input name="file" type="file" accept="image/jpeg,image/png,image/webp,video/mp4,video/webm" required /></label><button>Upload media</button></form><h2>Gallery editor</h2><form onSubmit={createGallery}><label>Gallery title<input name="gallery-title" required /></label><label>Access policy<select name="gallery-policy">{policies.map((policy) => <option key={policy}>{policy}</option>)}</select></label><label>PPV price (minor units; only when PPV)<input name="gallery-price-minor" type="number" min="1" step="1" /></label><label>PPV currency<input name="gallery-currency" defaultValue="EUR" maxLength={3} /></label><label>Public preview images<input name="gallery-preview-count" type="number" min="0" max="100" defaultValue="1" /></label><label>Cover image<select name="gallery-cover"><option value="">First selected image</option>{readyImages.map((asset) => <option key={asset.id} value={asset.id}>{asset.id}</option>)}</select></label><fieldset><legend>Ready images</legend>{readyImages.map((asset) => <label key={asset.id}><input name="image" type="checkbox" value={asset.id} />{asset.id}</label>)}</fieldset><button>Create and submit gallery</button></form><h2>Video editor</h2><form onSubmit={createVideo}><label>Video title<input name="video-title" required /></label><label>Access policy<select name="video-policy">{policies.map((policy) => <option key={policy}>{policy}</option>)}</select></label><label>PPV price (minor units; only when PPV)<input name="video-price-minor" type="number" min="1" step="1" /></label><label>PPV currency<input name="video-currency" defaultValue="EUR" maxLength={3} /></label><label>Preview starts at (seconds)<input name="video-preview-start" type="number" min="0" defaultValue="0" /></label><label>Preview duration (seconds)<input name="video-preview-duration" type="number" min="1" max="120" defaultValue="20" /></label><label>Ready video<select name="video" required><option value="">Select media</option>{readyVideos.map((asset) => <option key={asset.id} value={asset.id}>{asset.id}</option>)}</select></label><button>Create video</button></form><h2>Library status</h2><ul>{assets.map((asset) => <li key={asset.id}>{asset.id}: {asset.status}</li>)}</ul><h2>Content</h2><ul>{content.map((item) => <li key={item.id}>{item.content_type}: {item.title} ({item.status}) {item.access_policy === "ppv" && ` — ${item.price_amount_minor} ${item.price_currency}`} {item.status === "processing" && <button onClick={() => submitForReview(item.id)}>Submit for review</button>}</li>)}</ul>{message && <p>{message}</p>}{error && <p className="error">{error}</p>}</section>;
}
