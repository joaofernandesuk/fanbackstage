"use client";

import { FormEvent, useEffect, useState } from "react";

import { api, apiMediaUrl, ApiError } from "../lib/api";

type Asset = { id: string; status: string; media_type?: string; display_path?: string };
type ProfileMedia = { kind: "avatar" | "cover"; media_asset_id: string; delivery_path: string; focal_x: number; focal_y: number };
type Profile = { profile_media?: ProfileMedia[] };

export function CreatorProfileMediaEditor({ assets, onSaved }: { assets: Asset[]; onSaved: () => Promise<void> }) {
  const [items, setItems] = useState<ProfileMedia[]>([]);
  const [message, setMessage] = useState("");
  const ready = assets.filter((asset) => asset.status === "ready" && asset.media_type === "image");

  async function refresh() {
    const profile = await api<Profile>("/creators/me");
    setItems(profile.profile_media ?? []);
  }
  useEffect(() => { void refresh(); }, []);

  async function save(event: FormEvent<HTMLFormElement>, kind: "avatar" | "cover") {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api(`/creators/me/media/${kind}`, {
        method: "PUT",
        body: JSON.stringify({
          media_asset_id: form.get("asset"),
          focal_x: Number(form.get("focal_x")),
          focal_y: Number(form.get("focal_y")),
        }),
      });
      setMessage(`${kind === "avatar" ? "Avatar" : "Cover"} saved from an approved public derivative.`);
      await refresh(); await onSaved();
    } catch (error) { setMessage(error instanceof ApiError ? error.message : "Profile media could not be saved"); }
  }
  async function remove(kind: "avatar" | "cover") {
    await api(`/creators/me/media/${kind}`, { method: "DELETE" });
    setMessage(`${kind === "avatar" ? "Avatar" : "Cover"} removed.`);
    await refresh(); await onSaved();
  }

  return <section aria-label="Profile media">
    <h2>Avatar and cover</h2><p>Choose ready, approved public images. Reposition the focal point without exposing the original upload.</p>
    {(["avatar", "cover"] as const).map((kind) => {
      const current = items.find((item) => item.kind === kind);
      return <form key={kind} onSubmit={(event) => void save(event, kind)}>
        <h3>{kind === "avatar" ? "Avatar" : "Cover image"}</h3>
        {current && <img alt={`Current ${kind} preview`} src={apiMediaUrl(current.delivery_path)} style={{ aspectRatio: kind === "avatar" ? "1" : "16 / 6", objectFit: "cover", objectPosition: `${current.focal_x * 100}% ${current.focal_y * 100}%`, width: kind === "avatar" ? 140 : "100%", maxHeight: 260 }} />}
        <label>Ready image<select defaultValue={current?.media_asset_id ?? ""} name="asset" required><option disabled value="">Choose an image</option>{ready.map((asset) => <option key={asset.id} value={asset.id}>{asset.id.slice(0, 8)}</option>)}</select></label>
        <label>Horizontal focus<input defaultValue={current?.focal_x ?? .5} max="1" min="0" name="focal_x" step=".05" type="range" /></label>
        <label>Vertical focus<input defaultValue={current?.focal_y ?? .5} max="1" min="0" name="focal_y" step=".05" type="range" /></label>
        <button type="submit">Save {kind}</button>{current && <button onClick={() => void remove(kind)} type="button">Remove</button>}
      </form>;
    })}
    {!ready.length && <p>Upload and process a public-safe image before assigning profile media.</p>}{message && <p role="status">{message}</p>}
  </section>;
}
